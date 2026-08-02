"""Opt-in broker checks; set RABBITMQ_TEST_URL explicitly to run them."""
from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass

import pytest

from camera_ai.events import Event
from camera_ai.messaging.codecs import JSONMessageCodec
from camera_ai.messaging.config import RabbitMQSettings
from camera_ai.messaging.rabbit_connection import RabbitMQConnection
from camera_ai.messaging.rabbit_event_bus import RabbitMQEventBus
from camera_ai.messaging.rabbit_task_queue import RabbitMQTaskQueue

RABBIT_URL = os.getenv("RABBITMQ_TEST_URL")
pytestmark = pytest.mark.skipif(
    not RABBIT_URL,
    reason="set RABBITMQ_TEST_URL to run RabbitMQ integration tests",
)


@dataclass(frozen=True)
class DemoTask:
    task_id: str


def _codec() -> JSONMessageCodec[DemoTask]:
    return JSONMessageCodec(
        message_type="integration.demo",
        to_payload=lambda task: {"task_id": task.task_id},
        from_payload=lambda payload: DemoTask(task_id=str(payload["task_id"])),
    )


@pytest.mark.asyncio
async def test_event_fans_out_to_two_subscribers():
    settings = RabbitMQSettings(url=RABBIT_URL)
    connection = RabbitMQConnection(settings)
    publisher = RabbitMQEventBus(connection, settings)
    first, second = RabbitMQEventBus(connection, settings), RabbitMQEventBus(connection, settings)
    received = [asyncio.Event(), asyncio.Event()]
    suffix = uuid.uuid4().hex

    async def handler(index: int, _event: Event) -> None:
        received[index].set()

    subscriptions = [
        asyncio.create_task(first.subscribe("integration.*", lambda event: handler(0, event), subscriber_id=f"it-a-{suffix}")),
        asyncio.create_task(second.subscribe("integration.*", lambda event: handler(1, event), subscriber_id=f"it-b-{suffix}")),
    ]
    try:
        await asyncio.sleep(0.2)
        await publisher.publish(Event("integration.ready", "cam-it", {"ok": True}))
        await asyncio.wait_for(asyncio.gather(*(item.wait() for item in received)), 5)
    finally:
        for subscription in subscriptions:
            subscription.cancel()
        await asyncio.gather(*subscriptions, return_exceptions=True)
        await publisher.close()
        await first.close()
        await second.close()
        await connection.close()


@pytest.mark.asyncio
async def test_task_is_delivered_once_and_acknowledged():
    settings = RabbitMQSettings(url=RABBIT_URL)
    connection = RabbitMQConnection(settings)
    name = f"it-{uuid.uuid4().hex}"
    queue = RabbitMQTaskQueue(name, _codec(), connection, settings)
    try:
        await queue.enqueue(DemoTask("one"), priority=2)
        delivery = await asyncio.wait_for(queue.receive(), 5)
        assert delivery.task == DemoTask("one")
        await delivery.ack()
    finally:
        await queue.close()
        await connection.close()
