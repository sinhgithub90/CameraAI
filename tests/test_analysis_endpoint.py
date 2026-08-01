"""API access to the aggregate async video analysis."""
from __future__ import annotations

import pytest

from apps.api import main
from camera_ai.analysis_store import InMemoryAnalysisStore, VideoAnalysis
from camera_ai.schemas import AlertLevel, SceneAnalysis
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
