import pytest

from camera_ai.messaging.inprocess import InProcessTaskQueue


@pytest.mark.asyncio
async def test_priority_ack_retry_and_reject():
    queue = InProcessTaskQueue[str](max_attempts=2)
    await queue.enqueue("low", priority=4)
    await queue.enqueue("high", priority=1)

    high = await queue.receive()
    assert high.task == "high"
    await high.retry()
    retried = await queue.receive()
    assert retried.task == "high"
    assert retried.attempt == 1
    await retried.ack()

    low = await queue.receive()
    assert low.task == "low"
    await low.reject()
    assert queue.depth == 0
    assert queue.dead_letters == ("low",)


@pytest.mark.asyncio
async def test_retry_exhaustion_moves_task_to_dead_letters():
    queue = InProcessTaskQueue[str](max_attempts=1)
    await queue.enqueue("bad", priority=2)
    first = await queue.receive()
    await first.retry()
    second = await queue.receive()
    await second.retry()
    assert queue.dead_letters == ("bad",)
