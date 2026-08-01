import pytest

from camera_ai.analysis_store import InMemoryAnalysisStore, VideoAnalysis
from camera_ai.schemas import AlertLevel, SceneAnalysis, SecurityDecision, StageTiming, VideoWindowResult, VLMResult, QwenInputSummary


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
