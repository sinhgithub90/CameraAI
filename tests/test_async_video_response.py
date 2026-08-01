"""Async video endpoint exposes one queued alert per motion window."""
from __future__ import annotations

from io import BytesIO

import numpy as np
import pytest
from fastapi import UploadFile

from apps.api import main
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
async def test_async_video_enqueues_and_exposes_every_motion_window(monkeypatch):
    windows = [_window(0, "person"), _window(1, "car")]
    queue = RecordingQueue()
    store = RecordingAlertStore()

    monkeypatch.setattr(main.pipeline, "detect_video_windows", lambda event: windows)
    monkeypatch.setattr(main, "vlm_queue", queue)
    monkeypatch.setattr(main, "alert_store", store)

    upload = UploadFile(filename="clip.mp4", file=BytesIO(b"video-bytes"))
    result = await main.analyze_video_async(upload, camera_id="cam_01")

    assert len(result.alert_ids) == 2
    assert len(result.video_windows) == 2
    assert [window.alert_id for window in result.video_windows] == result.alert_ids
    assert [window.keyframes for window in result.video_windows] == [2, 2]
    assert [window.timing.motion_ms for window in result.video_windows] == [10.0, 11.0]
    assert [window.timing.detector_ms for window in result.video_windows] == [20.0, 21.0]
    assert [window.qwen_input.frame_indices for window in result.video_windows] == [
        [0, 5],
        [10, 15],
    ]
    assert [task.alert_id for task in queue.tasks] == result.alert_ids
    assert [alert.id for alert in store.alerts] == result.alert_ids
    assert result.annotated_image is not None
