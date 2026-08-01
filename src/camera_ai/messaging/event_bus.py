"""Transport-neutral event contracts and JSON mapping."""
from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from datetime import datetime, timezone
from typing import Any

from .codecs import JSONMessageCodec
from .contracts import JSONValue


class Event:
    """A JSON-safe event published through the system event bus."""

    def __init__(
        self,
        type: str,
        camera_id: str,
        payload: dict[str, JSONValue],
        source: str = "unknown",
        *,
        message_id: str | None = None,
        timestamp: float | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        schema_version: int = 1,
    ) -> None:
        self.type = type
        self.camera_id = camera_id
        self.payload = payload
        self.source = source
        self.message_id = message_id or uuid.uuid4().hex
        self.timestamp = time.time() if timestamp is None else timestamp
        self.correlation_id = correlation_id
        self.causation_id = causation_id
        self.schema_version = schema_version

    def __repr__(self) -> str:
        return (
            f"Event(type={self.type!r}, source={self.source!r}, "
            f"camera={self.camera_id!r}, message_id={self.message_id!r})"
        )


EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus(ABC):
    """Broadcast event bus. Each subscription receives its own delivery."""

    @abstractmethod
    async def publish(self, event: Event) -> None:
        """Publish an event to every matching subscription."""

    @abstractmethod
    async def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
        *,
        subscriber_id: str | None = None,
    ) -> None:
        """Run a subscription until cancelled or the bus is closed."""

    @abstractmethod
    async def close(self) -> None:
        """Release resources. Implementations must be idempotent."""


_PAYLOAD_CODEC = JSONMessageCodec[dict[str, JSONValue]](
    message_type="event.payload",
    to_payload=lambda payload: payload,
    from_payload=lambda payload: dict(payload),
)


def encode_event(event: Event) -> bytes:
    """Encode a system event to a strict JSON envelope."""
    return _PAYLOAD_CODEC.encode(
        event.payload,
        message_type=event.type,
        message_id=event.message_id,
        source=event.source,
        camera_id=event.camera_id,
        occurred_at=datetime.fromtimestamp(event.timestamp, tz=timezone.utc),
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
    )


def decode_event(body: bytes) -> Event:
    """Decode a strict JSON envelope into a new, independent Event object."""
    decoded = _PAYLOAD_CODEC.decode(body)
    envelope = decoded.envelope
    return Event(
        envelope.message_type,
        envelope.camera_id,
        dict(decoded.value),
        source=envelope.source,
        message_id=envelope.message_id,
        timestamp=envelope.occurred_at.timestamp(),
        correlation_id=envelope.correlation_id,
        causation_id=envelope.causation_id,
        schema_version=envelope.schema_version,
    )
