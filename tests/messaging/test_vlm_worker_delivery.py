import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from camera_ai.messaging.contracts import RejectMessageError
from camera_ai.queue import VLMQueue, VLMTask, VLMWorker
from camera_ai.schemas import AlertLevel, SceneAnalysis


def _task(task_id: str) -> VLMTask:
    return VLMTask(
        task_id=task_id,
        camera_id="cam-01",
        alert_id=f"alert-{task_id}",
        frames=[np.zeros((4, 4, 3), dtype=np.uint8)],
        priority=2,
        enqueued_at=time.monotonic(),
    )


async def _wait_until(predicate, timeout: float = 1.0) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout)


@pytest.mark.asyncio
async def test_worker_retries_transient_pipeline_error_then_succeeds():
    queue = VLMQueue(max_attempts=2)
    alert_store = AsyncMock()
    analysis = SceneAnalysis(summary="done", alert_level=AlertLevel.LOW)
    pipeline = MagicMock()
    pipeline.analyze_vlm = MagicMock(side_effect=[RuntimeError("temporary"), analysis])
    worker = VLMWorker(
        queue=queue,
        pipeline=pipeline,
        alert_store=alert_store,
        event_bus=AsyncMock(),
    )
    await worker.start()
    try:
        await queue.enqueue(_task("retry"))
        await _wait_until(lambda: pipeline.analyze_vlm.call_count == 2)
        await _wait_until(lambda: alert_store.update_vlm.await_count == 1)
        assert queue.dead_letters == ()
    finally:
        await worker.stop()


@pytest.mark.asyncio
async def test_worker_rejects_permanent_message_error():
    queue = VLMQueue(max_attempts=2)
    alert_store = AsyncMock()
    pipeline = MagicMock()
    pipeline.analyze_vlm = MagicMock(side_effect=RejectMessageError("invalid task"))
    worker = VLMWorker(
        queue=queue,
        pipeline=pipeline,
        alert_store=alert_store,
        event_bus=AsyncMock(),
    )
    await worker.start()
    try:
        await queue.enqueue(_task("reject"))
        await _wait_until(lambda: len(queue.dead_letters) == 1)
        assert queue.dead_letters[0].task_id == "reject"
        alert_store.update_vlm.assert_not_awaited()
    finally:
        await worker.stop()
