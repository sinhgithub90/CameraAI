import pytest

from camera_ai.analysis_store import InMemoryAnalysisStore, VideoAnalysis
from camera_ai.schemas import (
    AlertLevel,
    QwenInputSummary,
    SceneAnalysis,
    SecurityDecision,
    StageTiming,
    VideoWindowObservation,
    VideoWindowResult,
    VLMResult,
)
from camera_ai.video_windows import ProcessedVideoWindow
from camera_ai.vlm_policy import VLMCallDecision, VLMCallReason


def pending_window(alert_id: str) -> VideoWindowResult:
    return VideoWindowResult(
        alert_id=alert_id,
        window_index=0,
        start_seconds=0,
        end_seconds=5,
        vlm=VLMResult(summary="", status="pending"),
        security=SecurityDecision(alert_level=AlertLevel.LOW),
        qwen_input=QwenInputSummary(frame_count=2),
        timing=StageTiming(motion_ms=12.0, detector_ms=34.0, total_ms=46.0),
    )


@pytest.mark.asyncio
async def test_analysis_store_updates_window_and_aggregate_timing():
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-1", camera_id="cam_01"))
    await store.append_window("analysis-1", pending_window("alert-1"))
    await store.complete_window(
        "analysis-1",
        "alert-1",
        SceneAnalysis(summary="done", alert_level=AlertLevel.MEDIUM),
        qwen_ms=56.0,
    )

    analysis = await store.get("analysis-1")
    assert analysis is not None
    assert analysis.windows[0].vlm.status == "completed"
    assert analysis.windows[0].timing.qwen_ms == 56.0
    assert analysis.total_timing.total_ms == 102.0


@pytest.mark.asyncio
async def test_analysis_store_marks_completed_only_after_producer_finishes():
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-2", camera_id="cam_01"))
    await store.append_window("analysis-2", pending_window("alert-2"))
    await store.mark_producer_complete("analysis-2")

    queued = await store.get("analysis-2")
    assert queued is not None
    assert queued.status == "queued"

    await store.complete_window(
        "analysis-2",
        "alert-2",
        SceneAnalysis(summary="done", alert_level=AlertLevel.LOW),
        qwen_ms=10.0,
    )
    completed = await store.get("analysis-2")
    assert completed is not None
    assert completed.status == "completed"


@pytest.mark.asyncio
async def test_analysis_store_exposes_producer_failure():
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-3", camera_id="cam_01"))

    await store.mark_producer_failed("analysis-3", "cannot read video")

    analysis = await store.get("analysis-3")
    assert analysis is not None
    assert analysis.status == "failed"
    assert analysis.error == "cannot read video"


@pytest.mark.asyncio
async def test_processed_static_window_is_persisted_as_skipped_and_completes():
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-4", camera_id="cam_01"))
    await store.append_window("analysis-4", pending_window("alert-4"))
    await store.mark_producer_complete("analysis-4")
    processed = ProcessedVideoWindow(
        scene=SceneAnalysis(summary="static", alert_level=AlertLevel.LOW),
        qwen_input=QwenInputSummary(frame_count=2),
        timing=StageTiming(total_ms=10.0, qwen_ms=0.0),
        observation=VideoWindowObservation(
            camera_id="cam_01",
            window_id="cam_01_000000",
            start_ms=0,
            end_ms=5000,
        ),
        vlm_call=VLMCallDecision(
            call_vlm=False,
            reason=VLMCallReason.STATIC_WINDOW,
        ),
    )

    await store.complete_processed_window("analysis-4", "alert-4", processed, 0.0)

    analysis = await store.get("analysis-4")
    assert analysis is not None
    saved = analysis.windows[0]
    assert saved.vlm.status == "skipped"
    assert saved.vlm.skipped is True
    assert saved.event_metadata["vlm_call"] == {
        "call_vlm": False,
        "reason": "static_window",
        "priority": "low",
        "candidate_id": None,
    }
    assert saved.timing.qwen_ms == 0
    assert analysis.status == "completed"
