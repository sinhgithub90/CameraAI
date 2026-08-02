"""RabbitMQ implementation of generic competing-consumer task queues."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Generic, TypeVar

from .config import RabbitMQSettings
from .contracts import Delivery, MessageCodec, SerializationError, TaskQueue
from .rabbit_connection import RabbitMQConnection
from .rabbit_event_bus import MessageFactory, _declare_exchange, _default_message

T = TypeVar("T")


class RabbitMQTaskQueue(TaskQueue[T], Generic[T]):
    """Durable priority queue; each manual delivery is owned by one worker."""

    def __init__(
        self,
        name: str,
        codec: MessageCodec[T],
        connection: RabbitMQConnection,
        settings: RabbitMQSettings,
        *,
        message_factory: MessageFactory | None = None,
        owns_connection: bool = False,
    ) -> None:
        self.name = name
        self._codec = codec
        self._connection = connection
        self._settings = settings
        self._message_factory = message_factory or _default_message
        self._owns_connection = owns_connection
        self._publisher_channel: Any | None = None
        self._consumer_channel: Any | None = None
        self._queue: Any | None = None
        self._iterator_context: Any | None = None
        self._iterator: Any | None = None
        self._known_depth = 0
        self._closed = False

    async def enqueue(self, task: T, *, priority: int = 3) -> None:
        self._ensure_open()
        mapped = map_priority(priority, max_priority=self._settings.max_priority)
        channel = await self._publisher()
        exchange = await _declare_exchange(channel, self._settings.task_exchange, "direct")
        body = self._codec.encode(task, source="task-producer", camera_id="unknown")
        decoded = self._codec.decode(body)
        message = self._message_factory(
            body,
            content_type="application/json",
            message_id=decoded.envelope.message_id,
            correlation_id=decoded.envelope.correlation_id,
            headers={"x-attempt": 0},
            priority=mapped,
        )
        await exchange.publish(message, routing_key=self.name, mandatory=True)
        self._known_depth += 1

    async def receive(self) -> Delivery[T]:
        self._ensure_open()
        await self._ensure_iterator()
        while True:
            message = await self._iterator.__anext__()
            self._known_depth = max(0, self._known_depth - 1)
            try:
                decoded = self._codec.decode(message.body)
            except SerializationError:
                await message.reject(requeue=False)
                continue
            attempt = int((getattr(message, "headers", None) or {}).get("x-attempt", 0))
            priority = int(getattr(message, "priority", 0) or 0)
            return self._delivery(message, decoded.value, decoded.envelope, attempt, priority)

    @property
    def depth(self) -> int:
        return self._known_depth

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._iterator_context is not None:
            await self._iterator_context.__aexit__(None, None, None)
        channels = [item for item in (self._publisher_channel, self._consumer_channel) if item is not None]
        await asyncio.gather(*(channel.close() for channel in channels), return_exceptions=True)
        if self._owns_connection:
            await self._connection.close()

    async def _publisher(self) -> Any:
        if self._publisher_channel is None:
            self._publisher_channel = await self._connection.channel()
        return self._publisher_channel

    async def _ensure_iterator(self) -> None:
        if self._iterator is not None:
            return
        channel = await self._connection.channel(prefetch_count=self._settings.prefetch_vlm)
        self._consumer_channel = channel
        self._queue = await self._declare_queue(channel)
        self._iterator_context = self._queue.iterator(no_ack=False)
        self._iterator = await self._iterator_context.__aenter__()

    async def _declare_queue(self, channel: Any) -> Any:
        main_name = f"{self._settings.task_exchange}.{self.name}"
        retry_name = f"{main_name}.retry"
        dead_name = f"{main_name}.dlq"
        dead_key = f"task.{self.name}.dead"
        tasks = await _declare_exchange(channel, self._settings.task_exchange, "direct")
        dead = await _declare_exchange(channel, self._settings.dead_exchange, "direct")
        queue = await channel.declare_queue(
            main_name,
            durable=True,
            arguments={
                "x-max-priority": self._settings.max_priority,
                "x-dead-letter-exchange": self._settings.dead_exchange,
                "x-dead-letter-routing-key": dead_key,
            },
        )
        await queue.bind(tasks, routing_key=self.name)
        await channel.declare_queue(
            retry_name,
            durable=True,
            arguments={
                "x-message-ttl": self._settings.retry_delay_ms,
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": main_name,
            },
        )
        dead_queue = await channel.declare_queue(dead_name, durable=True)
        await dead_queue.bind(dead, routing_key=dead_key)
        return queue

    def _delivery(self, message: Any, task: T, envelope: Any, attempt: int, priority: int) -> Delivery[T]:
        async def ack() -> None:
            await message.ack()

        async def retry() -> None:
            if attempt >= self._settings.max_attempts:
                await message.reject(requeue=False)
                return
            retry_name = f"{self._settings.task_exchange}.{self.name}.retry"
            retry_message = self._message_factory(
                message.body,
                content_type="application/json",
                message_id=envelope.message_id,
                correlation_id=envelope.correlation_id,
                headers={"x-attempt": attempt + 1},
                priority=priority,
            )
            await self._consumer_channel.default_exchange.publish(
                retry_message, routing_key=retry_name, mandatory=True
            )
            await message.ack()

        async def reject() -> None:
            await message.reject(requeue=False)

        return Delivery(
            task=task,
            attempt=attempt,
            ack_callback=ack,
            retry_callback=retry,
            reject_callback=reject,
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("task queue is closed")


def map_priority(priority: int, *, max_priority: int) -> int:
    if not 1 <= priority <= max_priority:
        raise ValueError(f"priority must be in 1..{max_priority}")
    return max_priority + 1 - priority
