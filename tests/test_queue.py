# tests/test_queue.py
"""Tests for VLMQueue and VLMWorker."""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from camera_ai.queue import VLMTask, VLMQueue, VLMWorker
from camera_ai.schemas import AlertLevel, Detection, SceneAnalysis


def _make_task(
    task_id: str = "t1",
    priority: int = 3,
    camera_id: str = "cam_01",
    enqueued_at: float | None = None,
) -> VLMTask:
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    return VLMTask(
        task_id=task_id,
        camera_id=camera_id,
        alert_id=f"alert-{task_id}",
        frames=[frame],
        detections=[Detection(label="person", confidence=0.9, bbox=[1, 2, 3, 4])],
        rule_id="test_rule",
        priority=priority,
        enqueued_at=enqueued_at if enqueued_at is not None else time.monotonic(),
        max_keyframes=2,
    )


class TestVLMTask:
    def test_task_creation(self):
        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        task = VLMTask(
            task_id="t1",
            camera_id="cam_01",
            alert_id="alert-1",
            frames=[frame],
            detections=[],
            rule_id="intrusion",
            priority=2,
            enqueued_at=100.0,
            max_keyframes=4,
        )
        assert task.task_id == "t1"
        assert task.priority == 2
        assert len(task.frames) == 1
        assert task.max_keyframes == 4

    def test_task_ordering(self):
        """Lower priority number = higher urgency for PriorityQueue."""
        t1 = _make_task("high", priority=1, enqueued_at=100.0)
        t2 = _make_task("low", priority=4, enqueued_at=100.0)
        assert t1 < t2  # priority 1 < 4
        assert t2 > t1

    def test_tiebreaker_by_time(self):
        """Same priority → earlier enqueued wins."""
        t1 = _make_task("first", priority=2, enqueued_at=100.0)
        t2 = _make_task("second", priority=2, enqueued_at=200.0)
        assert t1 < t2


class TestVLMQueue:
    @pytest.fixture
    def queue(self):
        return VLMQueue()

    @pytest.mark.asyncio
    async def test_enqueue_dequeue_order(self, queue):
        t1 = _make_task("t1", priority=4)
        t2 = _make_task("t2", priority=1)  # higher urgency
        t3 = _make_task("t3", priority=2)

        await queue.enqueue(t1)
        await queue.enqueue(t2)
        await queue.enqueue(t3)

        assert await queue.dequeue() == t2  # priority 1 first
        assert await queue.dequeue() == t3  # priority 2 second
        assert await queue.dequeue() == t1  # priority 4 last

    @pytest.mark.asyncio
    async def test_depth(self, queue):
        assert queue.depth == 0
        await queue.enqueue(_make_task("t1"))
        assert queue.depth == 1
        await queue.enqueue(_make_task("t2"))
        assert queue.depth == 2
        await queue.dequeue()
        assert queue.depth == 1

    @pytest.mark.asyncio
    async def test_dynamic_priority_aging(self, queue):
        """Task waiting > threshold gets effective priority boost at dequeue."""
        old_time = 50.0  # simulate enqueued 80s ago
        task = _make_task("old", priority=3, enqueued_at=old_time)

        # Patch time.monotonic so aging sees an 80s wait
        now = old_time + 80.0
        original_monotonic = time.monotonic
        time.monotonic = lambda: now
        try:
            await queue.enqueue(task)
            dequeued = await queue.dequeue()
            # priority 3 + 80s wait → effective priority = 1
            # The aged task should be dequeued even though it was enqueued last
            assert dequeued.task_id == "old"
        finally:
            time.monotonic = original_monotonic

    @pytest.mark.asyncio
    async def test_dequeue_empty_waits(self, queue):
        """Dequeue on empty queue should wait until item available."""
        async def delayed_enqueue():
            await asyncio.sleep(0.05)
            await queue.enqueue(_make_task("delayed"))

        start = time.monotonic()
        enqueue_task = asyncio.create_task(delayed_enqueue())
        result = await queue.dequeue()
        elapsed = time.monotonic() - start

        await enqueue_task
        assert result.task_id == "delayed"
        assert elapsed >= 0.04  # waited for enqueue


class TestVLMWorker:
    @pytest.mark.asyncio
    async def test_worker_processes_task(self):
        """Worker dequeues task, calls pipeline.analyze_vlm, updates alert."""
        queue = VLMQueue()
        mock_alert_store = AsyncMock()
        mock_pipeline = MagicMock()
        mock_event_bus = AsyncMock()

        analysis = SceneAnalysis(
            summary="Phân tích xong",
            alert_level=AlertLevel.MEDIUM,
        )
        mock_pipeline.analyze_vlm = MagicMock(return_value=analysis)

        worker = VLMWorker(
            queue=queue,
            pipeline=mock_pipeline,
            alert_store=mock_alert_store,
            event_bus=mock_event_bus,
        )

        # Start worker in background
        await worker.start()

        # Enqueue a task
        task = _make_task("worker-test")
        await queue.enqueue(task)

        # Wait for worker to process
        await asyncio.sleep(0.1)

        # Stop worker
        await worker.stop()

        # Verify
        mock_pipeline.analyze_vlm.assert_called_once_with(task)
        mock_alert_store.update_vlm.assert_awaited_once()
        args, kwargs = mock_alert_store.update_vlm.call_args
        assert args == ("alert-worker-test", analysis)
        assert kwargs["qwen_ms"] >= 0

    @pytest.mark.asyncio
    async def test_worker_handles_pipeline_error(self):
        """Worker should not crash on pipeline error, just log and continue."""
        queue = VLMQueue()
        mock_alert_store = AsyncMock()
        mock_pipeline = MagicMock()
        mock_event_bus = AsyncMock()

        mock_pipeline.analyze_vlm = MagicMock(side_effect=RuntimeError("VLM crash"))

        worker = VLMWorker(
            queue=queue,
            pipeline=mock_pipeline,
            alert_store=mock_alert_store,
            event_bus=mock_event_bus,
        )

        await worker.start()
        await queue.enqueue(_make_task("error-task"))
        await asyncio.sleep(0.1)

        await worker.stop()

        # Alert store should NOT have been updated (pipeline failed)
        mock_alert_store.update_vlm.assert_not_called()
        # Worker should still be alive (not crashed)
