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
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse

from dotenv import load_dotenv

from camera_ai import SecurityAIPipeline
from camera_ai.alert_store import Alert, InMemoryAlertStore
from camera_ai.events import InProcessEventBus
from camera_ai.queue import VLMTask, VLMQueue, VLMWorker
from camera_ai.schemas import EventObject, MediaType, PipelineResult
from camera_ai.vlm.mock import MockAnalyzer

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load .env (repo root) before building the pipeline so env config applies.
load_dotenv()

UI_FILE = Path(__file__).resolve().parent / "static" / "index.html"


def _build_pipeline() -> SecurityAIPipeline:
    vlm_provider = os.getenv("CAMERA_AI_VLM", "ollama").strip().lower()
    if vlm_provider == "mock":
        logger.info("Using mock VLM (CAMERA_AI_VLM=mock)")
        return SecurityAIPipeline(vlm=MockAnalyzer())
    return SecurityAIPipeline()


pipeline = _build_pipeline()

app = FastAPI(title="Camera AI Demo", version="0.1.0")

# --- async pipeline infrastructure ---

event_bus = InProcessEventBus()
alert_store = InMemoryAlertStore(event_bus=event_bus)
vlm_queue = VLMQueue()
vlm_worker = VLMWorker(
    queue=vlm_queue,
    pipeline=pipeline,
    alert_store=alert_store,
    event_bus=event_bus,
)


@app.on_event("startup")
async def startup_vlm_worker() -> None:
    asyncio.create_task(vlm_worker.run())


@app.on_event("shutdown")
async def shutdown_vlm_worker() -> None:
    await vlm_worker.stop()


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return UI_FILE.read_text(encoding="utf-8")


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
        if frame is not None:
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
            frames=[frame] if frame is not None else [],
            detections=result.detections,
            rule_id="default",
            priority=3,  # Phase 2: Rule Engine sets this
            enqueued_at=time.monotonic(),
            max_keyframes=1,
        )
        alert = Alert(
            id=result.request_id,
            camera_id=camera_id,
            vlm=result.vlm,
            security=result.security,
        )
        await alert_store.create(alert)
        await vlm_queue.enqueue(task)

    return result


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
