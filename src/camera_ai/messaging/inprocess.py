"""Bounded asyncio implementations of messaging contracts."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Generic, TypeVar

from .contracts import Delivery, TaskQueue
from .event_bus import Event, EventBus, EventHandler, decode_event, encode_event

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass
class _QueuedEvent:
    event: Event
    attempt: int = 0


@dataclass
class _Subscription:
    pattern: str
    subscriber_id: str | None
    queue: asyncio.Queue[_QueuedEvent]
    task: asyncio.Task[object] | None = None


class InProcessEventBus(EventBus):
    """Async pub/sub bus with isolated bounded queues per subscription."""

    def __init__(self, *, max_queue_size: int = 1024, max_attempts: int = 3) -> None:
        if max_queue_size <= 0:
            raise ValueError("max_queue_size must be positive")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self._max_queue_size = max_queue_size
        self._max_attempts = max_attempts
        self._subscriptions: list[_Subscription] = []
        self._condition = asyncio.Condition()
        self._retry_tasks: set[asyncio.Task[object]] = set()
        self._closed = False

    async def publish(self, event: Event) -> None:
        if self._closed:
            raise RuntimeError("event bus is closed")
        encoded = encode_event(event)
        subscriptions = [
            item for item in tuple(self._subscriptions) if topic_matches(item.pattern, event.type)
        ]
        for subscription in subscriptions:
            await subscription.queue.put(_QueuedEvent(decode_event(encoded)))

    async def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
        *,
        subscriber_id: str | None = None,
    ) -> None:
        if self._closed:
            raise RuntimeError("event bus is closed")
        subscription = _Subscription(
            pattern=event_type,
            subscriber_id=subscriber_id,
            queue=asyncio.Queue(maxsize=self._max_queue_size),
            task=asyncio.current_task(),
        )
        async with self._condition:
            self._subscriptions.append(subscription)
            self._condition.notify_all()
        try:
            while True:
                queued = await subscription.queue.get()
                try:
                    await handler(queued.event)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception(
                        "event handler failed message_id=%s event_type=%s subscriber_id=%s attempt=%s",
                        queued.event.message_id,
                        queued.event.type,
                        subscriber_id,
                        queued.attempt,
                    )
                    if queued.attempt + 1 < self._max_attempts:
                        retry_task = asyncio.create_task(
                            subscription.queue.put(
                                _QueuedEvent(queued.event, attempt=queued.attempt + 1)
                            )
                        )
                        self._retry_tasks.add(retry_task)
                        retry_task.add_done_callback(self._retry_tasks.discard)
                    else:
                        logger.error(
                            "event retries exhausted message_id=%s event_type=%s subscriber_id=%s",
                            queued.event.message_id,
                            queued.event.type,
                            subscriber_id,
                        )
                finally:
                    subscription.queue.task_done()
        except asyncio.CancelledError:
            return
        finally:
            async with self._condition:
                try:
                    self._subscriptions.remove(subscription)
                except ValueError:
                    pass
                self._condition.notify_all()

    async def wait_until_subscribed(self, count: int, timeout: float = 1.0) -> None:
        """Wait until at least ``count`` subscriptions have registered."""
        async def wait() -> None:
            async with self._condition:
                await self._condition.wait_for(lambda: len(self._subscriptions) >= count)

        await asyncio.wait_for(wait(), timeout)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        current = asyncio.current_task()
        tasks = [
            subscription.task
            for subscription in tuple(self._subscriptions)
            if subscription.task is not None and subscription.task is not current
        ]
        for task in tasks:
            task.cancel()
        for task in tuple(self._retry_tasks):
            task.cancel()
        if tasks or self._retry_tasks:
            await asyncio.gather(*tasks, *self._retry_tasks, return_exceptions=True)
        async with self._condition:
            self._subscriptions.clear()
            self._condition.notify_all()


def topic_matches(pattern: str, event_type: str) -> bool:
    """Match AMQP-like ``*`` (one) and ``#`` (remaining) topic segments."""
    pattern_parts = pattern.split(".") if pattern else []
    type_parts = event_type.split(".") if event_type else []

    def matches(pattern_index: int, type_index: int) -> bool:
        if pattern_index == len(pattern_parts):
            return type_index == len(type_parts)
        part = pattern_parts[pattern_index]
        if part == "#":
            return any(matches(pattern_index + 1, index) for index in range(type_index, len(type_parts) + 1))
        if type_index == len(type_parts):
            return False
        if part == "*" or part == type_parts[type_index]:
            return matches(pattern_index + 1, type_index + 1)
        return False

    return matches(0, 0)


@dataclass(order=True)
class _QueuedTask(Generic[T]):
    priority: int
    sequence: int
    task: T = field(compare=False)
    attempt: int = field(default=0, compare=False)


class InProcessTaskQueue(TaskQueue[T], Generic[T]):
    """Bounded priority work queue with explicit delivery finalization."""

    def __init__(
        self,
        *,
        max_queue_size: int = 1024,
        max_attempts: int = 3,
        priority_resolver: Callable[[T, int], int] | None = None,
    ) -> None:
        if max_queue_size <= 0:
            raise ValueError("max_queue_size must be positive")
        if max_attempts < 0:
            raise ValueError("max_attempts must not be negative")
        self._queue: asyncio.PriorityQueue[_QueuedTask[T]] = asyncio.PriorityQueue(
            maxsize=max_queue_size
        )
        self._max_attempts = max_attempts
        self._priority_resolver = priority_resolver
        self._sequence = 0
        self._refresh_lock = asyncio.Lock()
        self._dead_letters: list[T] = []
        self._closed = False

    async def enqueue(self, task: T, *, priority: int = 3) -> None:
        await self._put(task, priority=priority, attempt=0)

    async def _put(self, task: T, *, priority: int, attempt: int) -> None:
        if self._closed:
            raise RuntimeError("task queue is closed")
        if priority < 1:
            raise ValueError("priority must be at least 1")
        self._sequence += 1
        await self._queue.put(
            _QueuedTask(priority=priority, sequence=self._sequence, task=task, attempt=attempt)
        )

    async def receive(self) -> Delivery[T]:
        await self._refresh_priorities()
        record = await self._queue.get()

        async def ack() -> None:
            self._queue.task_done()

        async def retry() -> None:
            self._queue.task_done()
            if record.attempt >= self._max_attempts:
                self._dead_letters.append(record.task)
                return
            await self._put(
                record.task,
                priority=record.priority,
                attempt=record.attempt + 1,
            )

        async def reject() -> None:
            self._queue.task_done()
            self._dead_letters.append(record.task)

        return Delivery(
            task=record.task,
            attempt=record.attempt,
            ack_callback=ack,
            retry_callback=retry,
            reject_callback=reject,
        )

    async def _refresh_priorities(self) -> None:
        if self._priority_resolver is None or self._queue.empty():
            return
        async with self._refresh_lock:
            records: list[_QueuedTask[T]] = []
            while True:
                try:
                    record = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self._queue.task_done()
                priority = self._priority_resolver(record.task, record.priority)
                if priority < 1:
                    raise ValueError("priority_resolver returned a value below 1")
                records.append(
                    _QueuedTask(
                        priority=priority,
                        sequence=record.sequence,
                        task=record.task,
                        attempt=record.attempt,
                    )
                )
            for record in records:
                self._queue.put_nowait(record)

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    @property
    def dead_letters(self) -> tuple[T, ...]:
        return tuple(self._dead_letters)

    async def remove_where(self, predicate: Callable[[T], bool]) -> list[T]:
        """Atomically remove ready tasks matching ``predicate``."""
        async with self._refresh_lock:
            kept: list[_QueuedTask[T]] = []
            removed: list[T] = []
            while True:
                try:
                    record = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self._queue.task_done()
                if predicate(record.task):
                    removed.append(record.task)
                else:
                    kept.append(record)
            for record in kept:
                self._queue.put_nowait(record)
            return removed

    async def close(self) -> None:
        self._closed = True
