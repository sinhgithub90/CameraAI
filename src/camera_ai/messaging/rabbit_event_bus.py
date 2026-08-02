"""RabbitMQ implementation of the broadcast EventBus contract."""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from typing import Any

from .config import RabbitMQSettings
from .contracts import RejectMessageError, SerializationError
from .event_bus import Event, EventBus, EventHandler, decode_event, encode_event
from .rabbit_connection import RabbitMQConnection

logger = logging.getLogger(__name__)
MessageFactory = Callable[..., Any]


class RabbitMQEventBus(EventBus):
    """Topic-exchange event bus with per-subscriber retry and DLQ queues."""

    def __init__(
        self,
        connection: RabbitMQConnection,
        settings: RabbitMQSettings,
        *,
        message_factory: MessageFactory | None = None,
        owns_connection: bool = False,
    ) -> None:
        self._connection = connection
        self._settings = settings
        self._message_factory = message_factory or _default_message
        self._owns_connection = owns_connection
        self._publisher_channel: Any | None = None
        self._consumer_channels: list[Any] = []
        self._closed = False

    async def publish(self, event: Event) -> None:
        self._ensure_open()
        channel = await self._publisher()
        exchange = await _declare_exchange(channel, self._settings.event_exchange, "topic")
        message = self._message_factory(
            encode_event(event),
            content_type="application/json",
            message_id=event.message_id,
            correlation_id=event.correlation_id,
            headers={"x-attempt": 0},
        )
        await exchange.publish(message, routing_key=event.type, mandatory=True)

    async def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
        *,
        subscriber_id: str | None = None,
    ) -> None:
        self._ensure_open()
        channel = await self._connection.channel(prefetch_count=self._settings.prefetch_events)
        self._consumer_channels.append(channel)
        queue, retry_queue = await self._declare_subscription(channel, event_type, subscriber_id)

        async def consume(message: Any) -> None:
            await self._handle_message(message, handler, channel, retry_queue, subscriber_id)

        tag = await queue.consume(consume, no_ack=False)
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            await queue.cancel(tag)
            raise
        finally:
            try:
                self._consumer_channels.remove(channel)
            except ValueError:
                pass
            await channel.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        channels = tuple(self._consumer_channels)
        self._consumer_channels.clear()
        if self._publisher_channel is not None:
            channels = (*channels, self._publisher_channel)
            self._publisher_channel = None
        await asyncio.gather(*(channel.close() for channel in channels), return_exceptions=True)
        if self._owns_connection:
            await self._connection.close()

    async def _publisher(self) -> Any:
        if self._publisher_channel is None:
            self._publisher_channel = await self._connection.channel()
        return self._publisher_channel

    async def _declare_subscription(
        self, channel: Any, event_type: str, subscriber_id: str | None
    ) -> tuple[Any, str]:
        stable = _queue_id(subscriber_id) if subscriber_id else f"ephemeral-{uuid.uuid4().hex}"
        main_name = f"{self._settings.event_exchange}.{stable}"
        retry_name = f"{main_name}.retry"
        dead_name = f"{main_name}.dlq"
        dead_key = f"event.{stable}.dead"
        events = await _declare_exchange(channel, self._settings.event_exchange, "topic")
        dead = await _declare_exchange(channel, self._settings.dead_exchange, "direct")
        durable = subscriber_id is not None
        main = await channel.declare_queue(
            main_name,
            durable=durable,
            exclusive=not durable,
            auto_delete=not durable,
            arguments={
                "x-dead-letter-exchange": self._settings.dead_exchange,
                "x-dead-letter-routing-key": dead_key,
            },
        )
        await main.bind(events, routing_key=event_type)
        retry = await channel.declare_queue(
            retry_name,
            durable=durable,
            exclusive=not durable,
            auto_delete=not durable,
            arguments={
                "x-message-ttl": self._settings.retry_delay_ms,
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": main_name,
            },
        )
        dead_queue = await channel.declare_queue(
            dead_name, durable=durable, exclusive=not durable, auto_delete=not durable
        )
        await dead_queue.bind(dead, routing_key=dead_key)
        return main, retry_name

    async def _handle_message(
        self,
        message: Any,
        handler: EventHandler,
        channel: Any,
        retry_queue: str,
        subscriber_id: str | None,
    ) -> None:
        try:
            event = decode_event(message.body)
        except SerializationError:
            logger.exception("rejecting malformed event subscriber_id=%s", subscriber_id)
            await message.reject(requeue=False)
            return
        attempt = int((getattr(message, "headers", None) or {}).get("x-attempt", 0))
        try:
            await handler(event)
        except RejectMessageError:
            logger.exception("rejecting event message_id=%s subscriber_id=%s", event.message_id, subscriber_id)
            await message.reject(requeue=False)
        except Exception:
            logger.exception(
                "event handler failed message_id=%s event_type=%s subscriber_id=%s attempt=%s",
                event.message_id, event.type, subscriber_id, attempt,
            )
            if attempt + 1 >= self._settings.max_attempts:
                await message.reject(requeue=False)
                return
            retry_message = self._message_factory(
                message.body,
                content_type="application/json",
                message_id=event.message_id,
                correlation_id=event.correlation_id,
                headers={"x-attempt": attempt + 1},
            )
            await channel.default_exchange.publish(
                retry_message, routing_key=retry_queue, mandatory=True
            )
            await message.ack()
        else:
            await message.ack()

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("event bus is closed")


def _queue_id(value: str) -> str:
    sanitized = re.sub(r"[^a-z0-9._-]", "-", value.lower()).strip(".-_")
    if not sanitized:
        raise ValueError("subscriber_id must contain a usable queue name")
    return sanitized


async def _declare_exchange(channel: Any, name: str, exchange_type: str) -> Any:
    return await channel.declare_exchange(name, type=exchange_type, durable=True)


def _default_message(body: bytes, **kwargs: Any) -> Any:
    try:
        import aio_pika
    except ModuleNotFoundError as exc:
        raise RuntimeError("RabbitMQ backend requires: pip install -e .[rabbitmq]") from exc
    return aio_pika.Message(
        body,
        content_type=kwargs.get("content_type"),
        message_id=kwargs.get("message_id"),
        correlation_id=kwargs.get("correlation_id"),
        headers=kwargs.get("headers"),
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )
