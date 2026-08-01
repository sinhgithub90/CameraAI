# src/camera_ai/events.py
"""Event bus — pub/sub for decoupled inter-component communication."""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable, Coroutine
from typing import Any


class Event:
    """An immutable message published on the event bus."""

    def __init__(
        self,
        type: str,
        camera_id: str,
        payload: dict[str, Any],
        source: str = "unknown",
    ) -> None:
        self.type = type
        self.camera_id = camera_id
        self.payload = payload
        self.source = source
        self.timestamp = time.time()

    def __repr__(self) -> str:
        return f"Event(type={self.type!r}, source={self.source!r}, camera={self.camera_id!r})"


# Handler nhận event, không trả về gì
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus(ABC):
    """Abstract event bus. Swap InProcessEventBus → RabbitMQEventBus via DI."""

    @abstractmethod
    async def publish(self, event: Event) -> None:
        """Publish an event to all subscribers of its type."""
        ...

    @abstractmethod
    async def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe to an event type. Runs until cancelled."""
        ...


class InProcessEventBus(EventBus):
    """asyncio.Queue-based event bus for single-process deployments.

    Pub/Sub broadcast: each subscriber gets its own private queue, so one
    published event is delivered to *every* subscriber of that event type
    (unlike a shared work queue where exactly one consumer would receive it).
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[Event]]] = defaultdict(list)

    async def publish(self, event: Event) -> None:
        # Snapshot: a subscriber may unsubscribe while we are broadcasting.
        for queue in list(self._subscribers[event.type]):
            await queue.put(event)

    async def subscribe(self, event_type: str, handler: EventHandler) -> None:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers[event_type].append(queue)
        try:
            while True:
                event = await queue.get()
                try:
                    await handler(event)
                except Exception:
                    # Don't let one bad handler kill the subscriber loop
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            try:
                self._subscribers[event_type].remove(queue)
            except ValueError:
                pass
