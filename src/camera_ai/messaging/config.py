"""Messaging backend settings. Reading settings never opens a connection."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

BackendName = Literal["inprocess", "rabbitmq"]


@dataclass(frozen=True)
class RabbitMQSettings:
    url: str = "amqp://guest:guest@localhost/"
    event_exchange: str = "camera_ai.events"
    task_exchange: str = "camera_ai.tasks"
    dead_exchange: str = "camera_ai.dead"
    prefetch_events: int = 32
    prefetch_vlm: int = 1
    max_priority: int = 5
    max_attempts: int = 3
    retry_delay_ms: int = 5000

    @classmethod
    def from_env(cls) -> "RabbitMQSettings":
        return cls(
            url=os.getenv("RABBITMQ_URL", cls.url),
            event_exchange=os.getenv("RABBITMQ_EVENT_EXCHANGE", cls.event_exchange),
            task_exchange=os.getenv("RABBITMQ_TASK_EXCHANGE", cls.task_exchange),
            dead_exchange=os.getenv("RABBITMQ_DEAD_EXCHANGE", cls.dead_exchange),
            prefetch_events=_positive_int("RABBITMQ_PREFETCH_EVENTS", cls.prefetch_events),
            prefetch_vlm=_positive_int("RABBITMQ_PREFETCH_VLM", cls.prefetch_vlm),
            max_priority=_positive_int("RABBITMQ_MAX_PRIORITY", cls.max_priority),
            max_attempts=_positive_int("RABBITMQ_MAX_ATTEMPTS", cls.max_attempts),
            retry_delay_ms=_positive_int("RABBITMQ_RETRY_DELAY_MS", cls.retry_delay_ms),
        )

    def __repr__(self) -> str:
        return (
            "RabbitMQSettings("
            f"url={_redact_url(self.url)!r}, event_exchange={self.event_exchange!r}, "
            f"task_exchange={self.task_exchange!r}, dead_exchange={self.dead_exchange!r}, "
            f"prefetch_events={self.prefetch_events}, prefetch_vlm={self.prefetch_vlm}, "
            f"max_priority={self.max_priority}, max_attempts={self.max_attempts}, "
            f"retry_delay_ms={self.retry_delay_ms})"
        )


@dataclass(frozen=True)
class MessagingSettings:
    event_bus_backend: BackendName = "inprocess"
    vlm_queue_backend: BackendName = "inprocess"
    rabbitmq: RabbitMQSettings = field(default_factory=RabbitMQSettings)

    @classmethod
    def from_env(cls) -> "MessagingSettings":
        return cls(
            event_bus_backend=_backend("CAMERA_AI_EVENT_BUS_BACKEND", "inprocess"),
            vlm_queue_backend=_backend("CAMERA_AI_VLM_QUEUE_BACKEND", "inprocess"),
            rabbitmq=RabbitMQSettings.from_env(),
        )


def _backend(name: str, default: BackendName) -> BackendName:
    value = os.getenv(name, default).strip().lower()
    if value not in {"inprocess", "rabbitmq"}:
        raise ValueError(f"{name} must be one of: inprocess|rabbitmq")
    return value  # type: ignore[return-value]


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _redact_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.password is None:
        return url
    username = parsed.username or ""
    host = parsed.hostname or ""
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, f"{username}:***@{host}", parsed.path, parsed.query, parsed.fragment))
