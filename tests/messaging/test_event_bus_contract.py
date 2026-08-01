import asyncio

import pytest

from camera_ai.events import Event, InProcessEventBus


@pytest.mark.asyncio
async def test_topic_wildcards_and_independent_payload_copies():
    bus = InProcessEventBus()
    received: list[tuple[str, Event]] = []
    ready = asyncio.Event()

    async def first(event: Event) -> None:
        event.payload["consumer"] = "first"
        received.append(("first", event))
        if len(received) == 2:
            ready.set()

    async def second(event: Event) -> None:
        received.append(("second", event))
        if len(received) == 2:
            ready.set()

    tasks = [
        asyncio.create_task(bus.subscribe("detection.*", first)),
        asyncio.create_task(bus.subscribe("#", second)),
    ]
    try:
        await bus.wait_until_subscribed(2)
        await bus.publish(Event("detection.completed", "cam-01", {"count": 1}))
        await asyncio.wait_for(ready.wait(), 1)
        second_event = next(event for name, event in received if name == "second")
        assert "consumer" not in second_event.payload
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bus.close()


@pytest.mark.asyncio
async def test_handler_retries_then_continues_with_next_event(caplog):
    bus = InProcessEventBus(max_attempts=2)
    attempts = 0
    completed = asyncio.Event()

    async def flaky(event: Event) -> None:
        nonlocal attempts
        attempts += 1
        if event.payload["id"] == 1:
            raise RuntimeError("broken")
        completed.set()

    task = asyncio.create_task(
        bus.subscribe("alert.created", flaky, subscriber_id="store")
    )
    try:
        await bus.wait_until_subscribed(1)
        await bus.publish(Event("alert.created", "cam", {"id": 1}))
        await bus.publish(Event("alert.created", "cam", {"id": 2}))
        await asyncio.wait_for(completed.wait(), 1)
        await asyncio.sleep(0)
        assert attempts == 3
        assert "broken" in caplog.text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await bus.close()


@pytest.mark.asyncio
async def test_bounded_queue_applies_back_pressure():
    bus = InProcessEventBus(max_queue_size=1)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_handler(_event: Event) -> None:
        entered.set()
        await release.wait()

    task = asyncio.create_task(bus.subscribe("frame.captured", blocked_handler))
    try:
        await bus.wait_until_subscribed(1)
        await bus.publish(Event("frame.captured", "cam", {"id": 1}))
        await asyncio.wait_for(entered.wait(), 1)
        await bus.publish(Event("frame.captured", "cam", {"id": 2}))
        third_publish = asyncio.create_task(
            bus.publish(Event("frame.captured", "cam", {"id": 3}))
        )
        await asyncio.sleep(0.05)
        assert not third_publish.done()
        release.set()
        await asyncio.wait_for(third_publish, 1)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await bus.close()
