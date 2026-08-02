"""Async video endpoint exposes one queued alert per motion window."""
from __future__ import annotations

import asyncio
from io import BytesIO

import numpy as np
import pytest
from fastapi import UploadFile

from apps.api import main
from camera_ai.alert_cooldown import (
    AlertRuntimePhase,
    InMemoryCameraAlertStateStore,
    WindowAdmission,
    WindowDisposition,
)
from camera_ai.schemas import AlertLevel
from camera_ai.analysis_store import InMemoryAnalysisStore, VideoAnalysis
from camera_ai.schemas import VideoFrameObservation
from camera_ai.video_windows import RawVideoWindow


class RecordingQueue:
    def __init__(self) -> None:
        self.tasks = []

    async def enqueue(self, task) -> None:
        self.tasks.append(task)


class RecordingAlertStore:
    def __init__(self) -> None:
        self.alerts = []

    async def create(self, alert) -> None:
        self.alerts.append(alert)

    async def delete(self, alert_id: str) -> None:
        self.alerts = [alert for alert in self.alerts if alert.id != alert_id]


class RejectingQueue(RecordingQueue):
    async def enqueue(self, task) -> bool:
        return False


class FailingAlertStore(RecordingAlertStore):
    async def create(self, alert) -> None:
        raise RuntimeError("alert persistence failed")


class RacingRedState:
    """First admission sees normal; the post-rejection read sees newly active red."""

    def admit_before_queue(self, **kwargs) -> WindowAdmission:
        return WindowAdmission.normal(
            stream_id=kwargs["stream_id"],
            camera_id=kwargs["camera_id"],
            start_seconds=kwargs["start_seconds"],
            end_seconds=kwargs["end_seconds"],
        )

    def inspect_window(self, **kwargs) -> WindowAdmission:
        return WindowAdmission(
            stream_id=kwargs["stream_id"],
            camera_id=kwargs["camera_id"],
            start_seconds=kwargs["start_seconds"],
            end_seconds=kwargs["end_seconds"],
            disposition=WindowDisposition.SUPPRESS,
            process_window=False,
            state=AlertRuntimePhase.ALERT_ACTIVE,
            effective_level=AlertLevel.HIGH,
            active_alert_id="episode-race",
            next_recheck_event_seconds=65.0,
        )


def _window(index: int, label: str) -> dict:
    frame = np.zeros((24, 32, 3), dtype=np.uint8)
    return RawVideoWindow(
        window_index=index,
        start_seconds=index * 5.0,
        observations=[
            VideoFrameObservation(frame_index=index * 10, timestamp_seconds=index * 5.0, frame=frame),
            VideoFrameObservation(frame_index=index * 10 + 5, timestamp_seconds=index * 5.0 + 1.0, frame=frame.copy()),
        ],
    )


@pytest.mark.asyncio
async def test_async_video_streams_each_window_to_the_global_queue(monkeypatch):
    windows = [_window(0, "person"), _window(1, "car")]
    queue = RecordingQueue()
    store = RecordingAlertStore()
    analysis_store = InMemoryAnalysisStore()

    def stream_video_chunks(event, on_window):
        for window in windows:
            on_window(window)

    monkeypatch.setattr(main.pipeline, "stream_video_chunks", stream_video_chunks)
    monkeypatch.setattr(main, "vlm_queue", queue)
    monkeypatch.setattr(main, "alert_store", store)
    monkeypatch.setattr(main, "analysis_store", analysis_store)

    upload = UploadFile(filename="clip.mp4", file=BytesIO(b"video-bytes"))
    result = await main.analyze_video_async(upload, camera_id="cam_01")

    assert result.request_id
    assert result.alert_ids == []
    assert result.video_windows == []

    await asyncio.sleep(0.1)

    analysis = await analysis_store.get(result.request_id)
    assert analysis is not None
    assert [window.window_index for window in analysis.windows] == [0, 1]
    assert [window.keyframes for window in analysis.windows] == [0, 0]
    assert [window.qwen_input.frame_indices for window in analysis.windows] == [
        [0, 5],
        [10, 15],
    ]
    assert [task.analysis_id for task in queue.tasks] == [result.request_id] * 2
    assert [task.window_index for task in queue.tasks] == [0, 1]
    assert [alert.id for alert in store.alerts] == [task.alert_id for task in queue.tasks]


@pytest.mark.asyncio
async def test_async_video_rejects_second_active_analysis_for_same_camera(monkeypatch):
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="already-running", camera_id="cam_01"))
    monkeypatch.setattr(main, "analysis_store", store)
    upload = UploadFile(filename="clip.mp4", file=BytesIO(b"video-bytes"))

    with pytest.raises(main.HTTPException) as exc_info:
        await main.analyze_video_async(upload, camera_id="cam_01")

    assert exc_info.value.status_code == 409
    assert "cam_01" in exc_info.value.detail


@pytest.mark.asyncio
async def test_known_red_window_is_dropped_before_records_and_global_queue(monkeypatch):
    """Catches cooldown windows consuming queue slots or compatibility records."""
    queue = RecordingQueue()
    alerts = RecordingAlertStore()
    analyses = InMemoryAnalysisStore()
    await analyses.create(VideoAnalysis(id="analysis-red", camera_id="cam-red"))
    state = InMemoryCameraAlertStateStore.seeded_red(
        stream_id="analysis-red",
        camera_id="cam-red",
        active_alert_id="episode-1",
        next_recheck_event_seconds=65.0,
    )
    monkeypatch.setattr(main, "vlm_queue", queue)
    monkeypatch.setattr(main, "alert_store", alerts)
    monkeypatch.setattr(main, "analysis_store", analyses)
    monkeypatch.setattr(main, "camera_alert_state_store", state)

    await main._enqueue_video_window("analysis-red", "cam-red", _window(1, "car"))

    analysis = await analyses.get("analysis-red")
    assert queue.tasks == []
    assert alerts.alerts == []
    assert analysis.windows == []
    assert analysis.cooldown.suppressed_windows == 1
    assert analysis.cooldown.recheck_at == 65.0


@pytest.mark.asyncio
async def test_only_one_due_recheck_window_enters_global_queue(monkeypatch):
    """Catches adjacent due windows bypassing the atomic reservation."""
    queue = RecordingQueue()
    alerts = RecordingAlertStore()
    analyses = InMemoryAnalysisStore()
    await analyses.create(VideoAnalysis(id="analysis-red", camera_id="cam-red"))
    state = InMemoryCameraAlertStateStore.seeded_red(
        stream_id="analysis-red",
        camera_id="cam-red",
        active_alert_id="episode-1",
        next_recheck_event_seconds=65.0,
    )
    monkeypatch.setattr(main, "vlm_queue", queue)
    monkeypatch.setattr(main, "alert_store", alerts)
    monkeypatch.setattr(main, "analysis_store", analyses)
    monkeypatch.setattr(main, "camera_alert_state_store", state)

    await main._enqueue_video_window("analysis-red", "cam-red", _window(13, "car"))
    await main._enqueue_video_window("analysis-red", "cam-red", _window(14, "car"))

    analysis = await analyses.get("analysis-red")
    assert len(queue.tasks) == 1
    assert queue.tasks[0].admission.reservation_version is not None
    assert len(analysis.windows) == 1
    assert analysis.cooldown.suppressed_windows == 1


@pytest.mark.asyncio
async def test_late_queue_cutoff_rejection_cleans_pending_records(monkeypatch):
    """Catches the red transition race leaving a pending window outside the queue."""
    queue = RejectingQueue()
    alerts = RecordingAlertStore()
    analyses = InMemoryAnalysisStore()
    await analyses.create(VideoAnalysis(id="analysis-race", camera_id="cam-race"))
    monkeypatch.setattr(main, "vlm_queue", queue)
    monkeypatch.setattr(main, "alert_store", alerts)
    monkeypatch.setattr(main, "analysis_store", analyses)
    monkeypatch.setattr(main, "camera_alert_state_store", RacingRedState())

    await main._enqueue_video_window(
        "analysis-race", "cam-race", _window(1, "person")
    )

    analysis = await analyses.get("analysis-race")
    assert analysis.windows == []
    assert alerts.alerts == []
    assert analysis.cooldown.active_alert_id == "episode-race"
    assert analysis.cooldown.suppressed_windows == 1


@pytest.mark.asyncio
async def test_recheck_persistence_failure_releases_reservation(monkeypatch):
    """Catches producer setup failure suppressing all future rechecks."""
    analyses = InMemoryAnalysisStore()
    await analyses.create(VideoAnalysis(id="analysis-fail", camera_id="cam-fail"))
    state = InMemoryCameraAlertStateStore.seeded_red(
        stream_id="analysis-fail",
        camera_id="cam-fail",
        active_alert_id="episode-1",
        next_recheck_event_seconds=65.0,
    )
    monkeypatch.setattr(main, "vlm_queue", RecordingQueue())
    monkeypatch.setattr(main, "alert_store", FailingAlertStore())
    monkeypatch.setattr(main, "analysis_store", analyses)
    monkeypatch.setattr(main, "camera_alert_state_store", state)

    with pytest.raises(RuntimeError, match="alert persistence failed"):
        await main._enqueue_video_window(
            "analysis-fail", "cam-fail", _window(13, "person")
        )

    analysis = await analyses.get("analysis-fail")
    runtime = state.get("analysis-fail", "cam-fail")
    assert analysis.windows == []
    assert runtime.recheck_reserved is False
    assert runtime.phase is AlertRuntimePhase.ALERT_ACTIVE_UNVERIFIED
