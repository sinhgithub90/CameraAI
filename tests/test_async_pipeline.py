# tests/test_async_pipeline.py
"""End-to-end test: detect → queue → worker → alert completion."""
import asyncio
import time

import cv2
import numpy as np
import pytest

from camera_ai import SecurityAIPipeline
from camera_ai.alert_store import Alert, InMemoryAlertStore
from camera_ai.events import InProcessEventBus
from camera_ai.queue import VLMTask, VLMQueue, VLMWorker
from camera_ai.schemas import EventObject, MediaType
from camera_ai.vlm.mock import MockAnalyzer


class DummyDetector:
    def detect(self, frame: np.ndarray):
        from camera_ai.schemas import Detection
        return [Detection(label="person", confidence=0.91, bbox=[1, 2, 3, 4])]


@pytest.mark.asyncio
async def test_detect_then_vlm_async_flow():
    """Image detection → VLM queue → worker → alert completed."""
    pipeline = SecurityAIPipeline(
        detector=DummyDetector(),
        vlm=MockAnalyzer(),
    )
    queue = VLMQueue()
    event_bus = InProcessEventBus()
    alert_store = InMemoryAlertStore(event_bus=event_bus)

    worker = VLMWorker(
        queue=queue,
        pipeline=pipeline,
        alert_store=alert_store,
        event_bus=event_bus,
    )
    await worker.start()

    # 1. Detect (fast)
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".png", frame)
    assert ok
    result = pipeline.detect(
        EventObject(image=buf.tobytes(), camera_id="e2e", media_type=MediaType.IMAGE)
    )
    assert result.vlm.status == "pending"

    # 2. Create alert + enqueue
    alert = Alert(
        id=result.request_id,
        camera_id="e2e",
        vlm=result.vlm,
        security=result.security,
    )
    await alert_store.create(alert)

    task = VLMTask(
        task_id="e2e-task",
        camera_id="e2e",
        alert_id=result.request_id,
        frames=[frame],
        detections=result.detections,
        rule_id="e2e_rule",
        priority=3,
        enqueued_at=time.monotonic(),
        max_keyframes=2,
    )
    await queue.enqueue(task)

    # 3. Wait for worker to process
    for _ in range(20):  # max 2s
        await asyncio.sleep(0.1)
        updated = await alert_store.get(result.request_id)
        if updated is not None and updated.vlm.status == "completed":
            break

    await worker.stop()

    # 4. Verify
    final = await alert_store.get(result.request_id)
    assert final is not None
    assert final.vlm.status == "completed"
    assert final.vlm.summary  # Mock trả về summary
    assert not final.vlm.skipped
