"""Construct messaging backends without wiring them into application startup."""
from __future__ import annotations

from typing import TypeVar

from .config import MessagingSettings
from .contracts import MessageCodec, TaskQueue
from .event_bus import EventBus
from .inprocess import InProcessEventBus, InProcessTaskQueue
from .rabbit_connection import RabbitMQConnection
from .rabbit_event_bus import RabbitMQEventBus
from .rabbit_task_queue import RabbitMQTaskQueue

T = TypeVar("T")


def build_event_bus(
    settings: MessagingSettings,
    *,
    connection: RabbitMQConnection | None = None,
) -> EventBus:
    if settings.event_bus_backend == "inprocess":
        return InProcessEventBus()
    rabbit = connection or RabbitMQConnection(settings.rabbitmq)
    return RabbitMQEventBus(
        rabbit,
        settings.rabbitmq,
        owns_connection=connection is None,
    )


def build_task_queue(
    name: str,
    settings: MessagingSettings,
    *,
    codec: MessageCodec[T] | None = None,
    connection: RabbitMQConnection | None = None,
) -> TaskQueue[T]:
    if settings.vlm_queue_backend == "inprocess":
        return InProcessTaskQueue()
    if codec is None:
        raise ValueError(
            "RabbitMQ task queues require a transport-safe codec; current VLMTask "
            "contains frames/raw_window, so add FrameStore and VLMJob first."
        )
    rabbit = connection or RabbitMQConnection(settings.rabbitmq)
    return RabbitMQTaskQueue(
        name,
        codec,
        rabbit,
        settings.rabbitmq,
        owns_connection=connection is None,
    )
