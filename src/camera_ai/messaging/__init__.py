"""Transport-neutral messaging contracts and backend adapters."""

from .contracts import (
    BackendUnavailableError,
    DecodedMessage,
    Delivery,
    DeliveryAlreadyFinalized,
    DeliveryState,
    MessageEnvelope,
    MessagingError,
    RejectMessageError,
    SerializationError,
    TaskQueue,
    UnsupportedSchemaError,
)
from .config import MessagingSettings, RabbitMQSettings
from .factory import build_event_bus, build_task_queue

__all__ = [
    "BackendUnavailableError",
    "build_event_bus",
    "build_task_queue",
    "DecodedMessage",
    "Delivery",
    "DeliveryAlreadyFinalized",
    "DeliveryState",
    "MessageEnvelope",
    "MessagingSettings",
    "MessagingError",
    "RejectMessageError",
    "RabbitMQSettings",
    "SerializationError",
    "TaskQueue",
    "UnsupportedSchemaError",
]
