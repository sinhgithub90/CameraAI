"""FastAPI demo adapter for the camera-AI core module.

This layer only translates HTTP <-> core objects. All logic lives in camera_ai,
so the same pipeline can later be driven from a queue worker unchanged.

Run (from repo root):
    python -m uvicorn apps.api.main:app --reload

Env knobs:
    CAMERA_AI_VLM   "ollama" (default) | "mock"   — force the mock VLM
    OLLAMA_BASE_URL default http://localhost:11434
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile

from dotenv import load_dotenv

from camera_ai import SecurityAIPipeline
from camera_ai.alert_cooldown import InMemoryCameraAlertStateStore
from camera_ai.analysis_store import (
    CompactVideoAnalysis,
    InMemoryAnalysisStore,
    VideoAnalysis,
)
from camera_ai.alert_store import Alert, InMemoryAlertStore
from camera_ai.events import InProcessEventBus
from camera_ai.queue import VLMTask, VLMQueue, VLMWorker
from camera_ai.schemas import (
    AlertLevel,
    EventObject,
    MediaType,
    PipelineResult,
    QwenInputSummary,
    SecurityDecision,
    StageTiming,
    VideoWindowResult,
    VLMResult,
)
from camera_ai.vlm.mock import MockAnalyzer
from camera_ai.video_windows import RawVideoWindow

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load .env (repo root) before building the pipeline so env config applies.
load_dotenv()

def _build_pipeline(
    alert_state_store: InMemoryCameraAlertStateStore | None = None,
) -> SecurityAIPipeline:
    vlm_provider = os.getenv("CAMERA_AI_VLM", "ollama").strip().lower()
    if vlm_provider == "mock":
        logger.info("Using mock VLM (CAMERA_AI_VLM=mock)")
        return SecurityAIPipeline(
            vlm=MockAnalyzer(),
            max_video_windows=None,
            alert_state_store=alert_state_store,
        )
    return SecurityAIPipeline(
        max_video_windows=None,
        alert_state_store=alert_state_store,
    )


camera_alert_state_store = InMemoryCameraAlertStateStore()
pipeline = _build_pipeline(camera_alert_state_store)

app = FastAPI(title="Camera AI Demo", version="0.1.0")

# --- async pipeline infrastructure ---

event_bus = InProcessEventBus()
alert_store = InMemoryAlertStore(event_bus=event_bus)
analysis_store = InMemoryAnalysisStore()
vlm_queue = VLMQueue()
vlm_worker = VLMWorker(
    queue=vlm_queue,
    pipeline=pipeline,
    alert_store=alert_store,
    event_bus=event_bus,
    analysis_store=analysis_store,
)


@app.on_event("startup")
async def startup_vlm_worker() -> None:
    await vlm_worker.start()


@app.on_event("shutdown")
async def shutdown_vlm_worker() -> None:
    await vlm_worker.stop()


@app.get("/health")
def health() -> dict[str, str | int]:
    return {
        "status": "ok",
        "vlm_queue_depth": vlm_queue.depth,
    }


@app.post("/analyze/image", response_model=PipelineResult)
async def analyze_image(
    file: UploadFile = File(...),
    camera_id: str = Form("unknown"),
) -> PipelineResult:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty upload")
    event = EventObject(camera_id=camera_id, image=content, media_type=MediaType.IMAGE)
    try:
        # pipeline.analyze_event is blocking (YOLO + VLM, ~5s) — run it off the
        # event loop so a slow request doesn't freeze every other endpoint.
        return await asyncio.to_thread(pipeline.analyze_event, event)
    except Exception as exc:
        logger.exception("image analysis failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/analyze/video", response_model=PipelineResult)
async def analyze_video(
    file: UploadFile = File(...),
    camera_id: str = Form("unknown"),
) -> PipelineResult:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty upload")
    event = EventObject(camera_id=camera_id, image=content, media_type=MediaType.VIDEO)
    try:
        return await asyncio.to_thread(pipeline.analyze_event, event)
    except Exception as exc:
        logger.exception("video analysis failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# --- async endpoints ---

VLM_MAX_SIDE = 640  # resize frame trước khi lưu vào VLMTask


async def _enqueue_video_window(
    analysis_id: str, camera_id: str, window: RawVideoWindow,
) -> None:
    """Persist one detected window, then enqueue it on the sole VLM queue."""
    alert_id = uuid.uuid4().hex
    observations = window.observations
    timing = StageTiming()
    window_result = VideoWindowResult(
        alert_id=alert_id,
        window_index=window.window_index,
        start_seconds=window.start_seconds,
        end_seconds=window.end_seconds,
        detections=[],
        vlm=VLMResult(summary="", status="pending"),
        security=SecurityDecision(alert_level=AlertLevel.LOW),
        keyframes=0,
        qwen_input=QwenInputSummary(
            frame_indices=[o.frame_index for o in observations],
            timestamps_seconds=[round(o.timestamp_seconds, 3) for o in observations],
            frame_count=0,
        ),
        timing=timing,
    )
    await analysis_store.append_window(analysis_id, window_result)
    await alert_store.create(
        Alert(
            id=alert_id,
            camera_id=camera_id,
            vlm=window_result.vlm,
            security=window_result.security,
            timing=timing,
        )
    )
    await vlm_queue.enqueue(
        VLMTask(
            task_id=alert_id,
            camera_id=camera_id,
            alert_id=alert_id,
            analysis_id=analysis_id,
            window_index=window.window_index,
            raw_window=window,
            rule_id="default",
            priority=3,
            enqueued_at=time.monotonic(),
            max_keyframes=pipeline.max_keyframes,
        )
    )


async def _produce_video_windows(
    event: EventObject, analysis_id: str, camera_id: str,
) -> None:
    """Run blocking decoding in a thread and submit each flush to asyncio."""
    loop = asyncio.get_running_loop()

    def on_window(window: RawVideoWindow) -> None:
        future = asyncio.run_coroutine_threadsafe(
            _enqueue_video_window(analysis_id, camera_id, window), loop
        )
        # Do not decode the next window until this one is visible and queued.
        future.result()

    try:
        await asyncio.to_thread(pipeline.stream_video_chunks, event, on_window)
    except Exception as exc:
        logger.exception("async video producer failed analysis=%s", analysis_id)
        await analysis_store.mark_producer_failed(analysis_id, str(exc))
    else:
        await analysis_store.mark_producer_complete(analysis_id)


@app.post("/async/analyze/image", response_model=PipelineResult)
async def analyze_image_async(
    file: UploadFile = File(...),
    camera_id: str = Form("unknown"),
) -> PipelineResult:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty upload")
    event = EventObject(camera_id=camera_id, image=content, media_type=MediaType.IMAGE)
    try:
        result = await asyncio.to_thread(pipeline.detect, event)
    except Exception as exc:
        logger.exception("async image detection failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if result.vlm.status == "pending":
        # Decode frame từ request bytes → lưu vào VLMTask.frames
        data = np.frombuffer(content, dtype=np.uint8)
        frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if frame is None:
            # Frame decode failed — VLM can't run. Mark degraded right away
            # so the alert never gets stuck in "pending" forever.
            result.vlm = VLMResult(
                summary="Không decode được ảnh — bỏ qua phân tích VLM.",
                degraded=True,
                status="completed",
            )
            logger.warning("async image: frame decode failed for alert=%s", result.request_id)
        else:
            h, w = frame.shape[:2]
            if max(h, w) > VLM_MAX_SIDE:
                scale = VLM_MAX_SIDE / max(h, w)
                frame = cv2.resize(
                    frame,
                    (int(w * scale), int(h * scale)),
                    interpolation=cv2.INTER_AREA,
                )

            task = VLMTask(
                camera_id=camera_id,
                alert_id=result.request_id,
                frames=[frame],
                detections=result.detections,
                rule_id="default",
                priority=3,  # Phase 2: Rule Engine sets this
                enqueued_at=time.monotonic(),
                max_keyframes=1,
            )
            await vlm_queue.enqueue(task)

        alert = Alert(
            id=result.request_id,
            camera_id=camera_id,
            vlm=result.vlm,
            security=result.security,
        )
        await alert_store.create(alert)

    return result


@app.post("/async/analyze/video", response_model=PipelineResult)
async def analyze_video_async(
    file: UploadFile = File(...),
    camera_id: str = Form("unknown"),
) -> PipelineResult:
    if await analysis_store.has_active_camera(camera_id):
        raise HTTPException(
            status_code=409,
            detail=f"camera {camera_id} already has an active video analysis",
        )
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty upload")
    event = EventObject(camera_id=camera_id, image=content, media_type=MediaType.VIDEO)
    result = PipelineResult(
        media_type=MediaType.VIDEO,
        camera_id=camera_id,
        vlm=VLMResult(
            summary="Đang đọc video theo từng cửa sổ 5 giây...",
            status="pending",
        ),
        security=SecurityDecision(alert_level=AlertLevel.LOW),
    )
    await analysis_store.create(
        VideoAnalysis(id=result.request_id, camera_id=camera_id)
    )
    asyncio.create_task(_produce_video_windows(event, result.request_id, camera_id))
    return result


@app.get(
    "/analyses/{analysis_id}",
    response_model=CompactVideoAnalysis,
    response_model_exclude_none=True,
)
async def get_analysis(analysis_id: str) -> CompactVideoAnalysis:
    analysis = await analysis_store.get(analysis_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="analysis not found")
    return CompactVideoAnalysis.from_analysis(analysis)


@app.get("/alerts/{alert_id}")
async def get_alert(alert_id: str) -> dict:
    alert = await alert_store.get(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    return alert.to_dict()


@app.get("/alerts")
async def list_alerts(camera_id: str | None = None) -> list[dict]:
    if camera_id:
        return [a.to_dict() for a in await alert_store.list_active(camera_id)]
    return [a.to_dict() for a in alert_store._alerts.values()]
