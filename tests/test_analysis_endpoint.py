"""API access to the aggregate async video analysis."""
from __future__ import annotations

import pytest

from apps.api import main
from camera_ai.analysis_store import InMemoryAnalysisStore, VideoAnalysis
from camera_ai.schemas import (
    AlertLevel,
    SceneAnalysis,
    SecurityDecision,
    VLMResult,
)
from test_analysis_store import pending_window


@pytest.mark.asyncio
async def test_get_analysis_returns_windows_and_total_timing(monkeypatch):
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="a1", camera_id="cam_01"))
    await store.append_window("a1", pending_window("alert-0"))
    await store.complete_window(
        "a1", "alert-0", SceneAnalysis(summary="done", alert_level=AlertLevel.LOW), 50.0
    )
    monkeypatch.setattr(main, "analysis_store", store)

    response = await main.get_analysis("a1")

    assert response.windows[0].timing.qwen_ms == 50.0
    assert response.total_timing.qwen_ms == 50.0


@pytest.mark.asyncio
async def test_get_analysis_exposes_compact_suppressed_window_without_internal_data(
    monkeypatch,
):
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="compact-1", camera_id="cam_01"))
    await store.append_window("compact-1", pending_window("alert-1"))
    analysis = await store.get("compact-1")
    internal = analysis.windows[0]
    internal.vlm = VLMResult(
        summary="Inherited active alert",
        skipped=True,
        status="suppressed",
    )
    internal.security = SecurityDecision(alert_level=AlertLevel.HIGH)
    internal.event_metadata = {
        "candidates": [{"candidate_id": "private-candidate"}],
        "vlm_trace": {"raw_output": "private-output"},
        "vlm_call": {
            "call_vlm": False,
            "reason": "active_alert_cooldown",
        },
        "alert_context": {
            "verification_status": "suppressed",
            "active_alert_id": "episode-1",
            "next_recheck_event_seconds": 65.0,
            "recheck": False,
            "episode_created": False,
            "episode_extended": False,
            "episode_resolved": False,
        },
    }
    monkeypatch.setattr(main, "analysis_store", store)

    response = await main.get_analysis("compact-1")
    serialized = response.model_dump(mode="json", exclude_none=True)
    window = serialized["windows"][0]

    assert window["alert_level"] == "high"
    assert window["qwen"] == {
        "status": "suppressed",
        "summary": "Inherited active alert",
        "degraded": False,
        "verified": False,
        "reason": "active_alert_cooldown",
    }
    assert window["cooldown"] == {
        "active_alert_id": "episode-1",
        "next_recheck_seconds": 65.0,
    }
    assert "detections" not in window
    assert "qwen_input" not in window
    assert "event_metadata" not in window
    assert "security" not in window
    assert "vlm" not in window
