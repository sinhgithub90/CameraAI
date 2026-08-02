"""Shared contracts for event and task transports."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Generic, Protocol, TypeAlias, TypeVar

JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
T = TypeVar("T")


class MessagingError(Exception):
    """Base exception for messaging failures."""


class SerializationError(MessagingError):
    """A value cannot be represented by the transport codec."""


class UnsupportedSchemaError(SerializationError):
    """The received message uses an unsupported schema version."""


class DeliveryAlreadyFinalized(MessagingError):
    """An ack/retry/reject operation was attempted twice."""


class RejectMessageError(MessagingError):
    """The handler identified a permanently invalid message."""


class BackendUnavailableError(MessagingError):
    """An optional messaging backend dependency is not installed."""


@dataclass(frozen=True)
class MessageEnvelope:
    message_id: str
    message_type: str
    source: str
    camera_id: str
    occurred_at: datetime
    correlation_id: str | None
    causation_id: str | None
    schema_version: int
    payload: Mapping[str, JSONValue]


@dataclass(frozen=True)
class DecodedMessage(Generic[T]):
    value: T
    envelope: MessageEnvelope


class MessageCodec(Protocol, Generic[T]):
    def encode(self, value: T, **metadata: object) -> bytes:
        """Encode a transport-safe message."""

    def decode(self, body: bytes) -> DecodedMessage[T]:
        """Decode a message received from a transport."""


class DeliveryState(str, Enum):
    PENDING = "pending"
    ACKED = "acked"
    RETRIED = "retried"
    REJECTED = "rejected"


DeliveryCallback = Callable[[], Awaitable[None]]


class Delivery(Generic[T]):
    """A task plus the one permitted terminal acknowledgement action."""

    def __init__(
        self,
        *,
        task: T,
        attempt: int,
        ack_callback: DeliveryCallback,
        retry_callback: DeliveryCallback,
        reject_callback: DeliveryCallback,
    ) -> None:
        self.task = task
        self.attempt = attempt
        self._state = DeliveryState.PENDING
        self._callbacks = {
            DeliveryState.ACKED: ack_callback,
            DeliveryState.RETRIED: retry_callback,
            DeliveryState.REJECTED: reject_callback,
        }

    @property
    def state(self) -> DeliveryState:
        return self._state

    async def _finalize(self, state: DeliveryState) -> None:
        if self._state is not DeliveryState.PENDING:
            raise DeliveryAlreadyFinalized(f"delivery already {self._state.value}")
        await self._callbacks[state]()
        self._state = state

    async def ack(self) -> None:
        await self._finalize(DeliveryState.ACKED)

    async def retry(self) -> None:
        await self._finalize(DeliveryState.RETRIED)

    async def reject(self) -> None:
        await self._finalize(DeliveryState.REJECTED)


class TaskQueue(ABC, Generic[T]):
    """A competing-consumer queue; each task is delivered once per attempt."""

    @abstractmethod
    async def enqueue(self, task: T, *, priority: int = 3) -> None:
        """Place one task into the queue."""

    @abstractmethod
    async def receive(self) -> Delivery[T]:
        """Wait for one delivery that must be finalized by the consumer."""

    @property
    @abstractmethod
    def depth(self) -> int:
        """Return locally known ready-message depth without network I/O."""

    @abstractmethod
    async def close(self) -> None:
        """Release backend resources. Must be idempotent."""
