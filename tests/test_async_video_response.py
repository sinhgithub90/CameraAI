"""Async video endpoint exposes one queued alert per motion window."""
from __future__ import annotations

import asyncio
from io import BytesIO

import numpy as np
import pytest
from fastapi import UploadFile

from apps.api import main
from camera_ai.analysis_store import InMemoryAnalysisStore
from camera_ai.schemas import Detection


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


def _window(index: int, label: str) -> dict:
    frame = np.zeros((24, 32, 3), dtype=np.uint8)
    return {
        "window_index": index,
        "start_seconds": index * 5.0,
        "frames": [frame, frame.copy()],
        "frame_indices": [index * 10, index * 10 + 5],
        "timestamps_seconds": [index * 5.0, index * 5.0 + 1.0],
        "motion_ms": 10.0 + index,
        "detector_ms": 20.0 + index,
        "detections": [
            Detection(label=label, confidence=0.9, bbox=[1, 1, 8, 8])
        ],
    }


@pytest.mark.asyncio
async def test_async_video_streams_each_window_to_the_global_queue(monkeypatch):
    windows = [_window(0, "person"), _window(1, "car")]
    queue = RecordingQueue()
    store = RecordingAlertStore()
    analysis_store = InMemoryAnalysisStore()

    def stream_video_windows(event, on_window):
        for window in windows:
            on_window(window)

    monkeypatch.setattr(main.pipeline, "stream_video_windows", stream_video_windows)
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
    assert [window.keyframes for window in analysis.windows] == [2, 2]
    assert [window.qwen_input.frame_indices for window in analysis.windows] == [
        [0, 5],
        [10, 15],
    ]
    assert [task.analysis_id for task in queue.tasks] == [result.request_id] * 2
    assert [task.window_index for task in queue.tasks] == [0, 1]
    assert [alert.id for alert in store.alerts] == [task.alert_id for task in queue.tasks]
