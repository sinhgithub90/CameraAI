# RabbitMQ Transport Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement tested in-process and RabbitMQ adapters for pub/sub events and competing-consumer task queues without enabling RabbitMQ in the current API runtime.

**Architecture:** Business code depends on separate `EventBus` and generic `TaskQueue[T]` contracts. Both backends share a versioned JSON envelope and delivery lifecycle; RabbitMQ adapters share a lazy robust connection but use different topologies. Existing imports and the default in-process API remain backward compatible.

**Tech Stack:** Python 3.11+, `asyncio`, Pydantic models already in the repository, optional `aio-pika>=9.5,<10`, pytest, pytest-asyncio, RabbitMQ AMQP 0-9-1.

## Global Constraints

- `apps/api/main.py` must continue to instantiate in-process implementations and must not connect to RabbitMQ during normal startup.
- `aio-pika` is an optional dependency; the base test suite must pass without it installed.
- Never serialize JPEG bytes, base64 images, `numpy.ndarray`, `RawVideoWindow`, pickle, or arbitrary Python objects into broker messages.
- Keep `EventBus` broadcast semantics separate from `TaskQueue` competing-consumer semantics.
- Preserve current two-argument calls to `EventBus.subscribe(event_type, handler)` and current `VLMQueue.enqueue/dequeue/depth` behavior.
- RabbitMQ delivery is at-least-once; consumers must use stable IDs and idempotent side effects.
- Domain priority uses lower integer = more urgent; the Rabbit adapter must invert it because AMQP uses higher integer = more urgent.
- Rabbit VLM runtime selection remains unavailable until a transport-safe `VLMJob` and `FrameStore` exist.
- Integration tests are opt-in through `RABBITMQ_TEST_URL`; tests must never start Docker or connect to localhost implicitly.

---

## File Structure

| Path | Responsibility |
|---|---|
| `src/camera_ai/messaging/contracts.py` | JSON types, envelope, errors, `MessageCodec`, `Delivery`, `TaskQueue` contracts |
| `src/camera_ai/messaging/codecs.py` | Strict versioned JSON codec and event/task codec adapters |
| `src/camera_ai/messaging/event_bus.py` | `Event`, event codec/mapper, handler type and `EventBus` contract |
| `src/camera_ai/messaging/inprocess.py` | Bounded in-process event bus and generic priority task queue |
| `src/camera_ai/messaging/config.py` | Backend/Rabbit settings parsed without establishing connections |
| `src/camera_ai/messaging/rabbit_connection.py` | Lazy `aio-pika` import, robust shared connection/channel lifecycle |
| `src/camera_ai/messaging/rabbit_event_bus.py` | Topic exchange, subscriber queues, local retry queues and event DLQs |
| `src/camera_ai/messaging/rabbit_task_queue.py` | Durable priority work queue, manual delivery lifecycle and task DLQ |
| `src/camera_ai/messaging/factory.py` | Backend construction without wiring it into the API |
| `src/camera_ai/events.py` | Backward-compatible event API facade |
| `src/camera_ai/queue.py` | VLM domain task/worker plus backward-compatible in-process `VLMQueue` |

---

### Task 1: Versioned Envelope, Strict JSON Codec, and Delivery Contract

**Files:**
- Create: `src/camera_ai/messaging/__init__.py`
- Create: `src/camera_ai/messaging/contracts.py`
- Create: `src/camera_ai/messaging/codecs.py`
- Create: `tests/messaging/test_codecs.py`
- Create: `tests/messaging/test_delivery.py`

**Interfaces:**
- Consumes: Python standard library only.
- Produces: `MessageEnvelope`, `MessageCodec[T]`, `JSONMessageCodec[T]`, `Delivery[T]`, `TaskQueue[T]`, `SerializationError`, `UnsupportedSchemaError`, `DeliveryAlreadyFinalized`, `RejectMessageError`.

- [ ] **Step 1: Write failing strict-codec tests**

Create `tests/messaging/test_codecs.py`:

```python
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pytest

from camera_ai.messaging.codecs import JSONMessageCodec
from camera_ai.messaging.contracts import SerializationError, UnsupportedSchemaError


@dataclass(frozen=True)
class DemoTask:
    task_id: str
    value: int


def _codec() -> JSONMessageCodec[DemoTask]:
    return JSONMessageCodec(
        message_type="demo.task",
        to_payload=lambda task: {"task_id": task.task_id, "value": task.value},
        from_payload=lambda payload: DemoTask(
            task_id=str(payload["task_id"]), value=int(payload["value"])
        ),
    )


def test_json_codec_round_trip_preserves_envelope_metadata():
    codec = _codec()
    occurred_at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    encoded = codec.encode(
        DemoTask("task-1", 7),
        message_id="message-1",
        source="rule-engine",
        camera_id="cam-01",
        occurred_at=occurred_at,
        correlation_id="analysis-1",
        causation_id="candidate-1",
    )
    decoded = codec.decode(encoded)
    assert decoded.value == DemoTask("task-1", 7)
    assert decoded.envelope.message_id == "message-1"
    assert decoded.envelope.occurred_at == occurred_at
    assert decoded.envelope.schema_version == 1


@pytest.mark.parametrize("unsafe", [b"jpeg", np.zeros((2, 2, 3)), float("nan")])
def test_json_codec_rejects_non_transport_values(unsafe):
    codec = JSONMessageCodec(
        message_type="unsafe",
        to_payload=lambda _value: {"unsafe": unsafe},
        from_payload=lambda payload: payload,
    )
    with pytest.raises(SerializationError):
        codec.encode(object(), source="test", camera_id="cam-01")


def test_json_codec_rejects_unknown_schema_version():
    body = _codec().encode(DemoTask("task-1", 1), source="test", camera_id="cam")
    mutated = body.replace(b'"schema_version":1', b'"schema_version":99')
    with pytest.raises(UnsupportedSchemaError):
        _codec().decode(mutated)
```

- [ ] **Step 2: Write failing delivery-finalization tests**

Create `tests/messaging/test_delivery.py`:

```python
from unittest.mock import AsyncMock

import pytest

from camera_ai.messaging.contracts import Delivery, DeliveryAlreadyFinalized


@pytest.mark.asyncio
async def test_delivery_can_be_finalized_exactly_once():
    ack, retry, reject = AsyncMock(), AsyncMock(), AsyncMock()
    delivery = Delivery(
        task="task-1", attempt=0,
        ack_callback=ack, retry_callback=retry, reject_callback=reject,
    )
    await delivery.ack()
    ack.assert_awaited_once()
    with pytest.raises(DeliveryAlreadyFinalized):
        await delivery.retry()
    retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_and_reject_use_their_own_callback():
    callbacks = [AsyncMock(), AsyncMock(), AsyncMock()]
    retry_delivery = Delivery(
        task="retry", attempt=2,
        ack_callback=callbacks[0], retry_callback=callbacks[1], reject_callback=callbacks[2],
    )
    await retry_delivery.retry()
    callbacks[1].assert_awaited_once()

    reject = AsyncMock()
    reject_delivery = Delivery(
        task="reject", attempt=0,
        ack_callback=AsyncMock(), retry_callback=AsyncMock(), reject_callback=reject,
    )
    await reject_delivery.reject()
    reject.assert_awaited_once()
```

- [ ] **Step 3: Run tests to verify missing contracts fail**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/messaging/test_codecs.py tests/messaging/test_delivery.py -q
```

Expected: collection fails because `camera_ai.messaging` does not exist.

- [ ] **Step 4: Implement contracts and strict JSON codec**

Create `src/camera_ai/messaging/contracts.py` with these exact public signatures:

```python
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


class MessagingError(Exception): pass
class SerializationError(MessagingError): pass
class UnsupportedSchemaError(SerializationError): pass
class DeliveryAlreadyFinalized(MessagingError): pass
class RejectMessageError(MessagingError): pass
class BackendUnavailableError(MessagingError): pass


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
        raise NotImplementedError
    def decode(self, body: bytes) -> DecodedMessage[T]:
        raise NotImplementedError


class DeliveryState(str, Enum):
    PENDING = "pending"
    ACKED = "acked"
    RETRIED = "retried"
    REJECTED = "rejected"


DeliveryCallback = Callable[[], Awaitable[None]]


class Delivery(Generic[T]):
    def __init__(self, *, task: T, attempt: int,
                 ack_callback: DeliveryCallback,
                 retry_callback: DeliveryCallback,
                 reject_callback: DeliveryCallback) -> None:
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
    @abstractmethod
    async def enqueue(self, task: T, *, priority: int = 3) -> None:
        raise NotImplementedError
    @abstractmethod
    async def receive(self) -> Delivery[T]:
        raise NotImplementedError
    @property
    @abstractmethod
    def depth(self) -> int:
        raise NotImplementedError
    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError
```

Implement `Delivery._finalize(state, callback)` so state changes only after the
callback succeeds; a failed Rabbit publish therefore leaves the original delivery
pending and eligible for broker redelivery.

Create `src/camera_ai/messaging/codecs.py` with `JSONMessageCodec[T]`. Encode with
`json.dumps(..., allow_nan=False, separators=(",", ":"), sort_keys=True)` and
UTC ISO-8601 timestamps. Decode UTF-8, require `schema_version == 1`, require an
object payload, then invoke `from_payload`. Wrap `TypeError`, `ValueError`,
`UnicodeDecodeError`, and `json.JSONDecodeError` as `SerializationError`.

Export the public contracts from `src/camera_ai/messaging/__init__.py`.

- [ ] **Step 5: Run focused tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/messaging/test_codecs.py tests/messaging/test_delivery.py -q
```

Expected: all tests pass.

Commit:

```powershell
git add src/camera_ai/messaging tests/messaging/test_codecs.py tests/messaging/test_delivery.py
git commit -m "feat: add transport envelope and delivery contracts"
```

---

### Task 2: Backward-Compatible In-Process EventBus

**Files:**
- Create: `src/camera_ai/messaging/event_bus.py`
- Create: `src/camera_ai/messaging/inprocess.py`
- Modify: `src/camera_ai/events.py:1-84`
- Modify: `tests/test_events.py`
- Create: `tests/messaging/test_event_bus_contract.py`

**Interfaces:**
- Consumes: `JSONMessageCodec`, `SerializationError` from Task 1.
- Produces: `Event`, `EventHandler`, `EventBus`, `InProcessEventBus(max_queue_size=1024, max_attempts=3)`, `topic_matches(pattern, event_type)`.

The exact abstract interface is:

```python
class EventBus(ABC):
    async def publish(self, event: Event) -> None:
        raise NotImplementedError
    async def subscribe(self, event_type: str, handler: EventHandler,
                        *, subscriber_id: str | None = None) -> None:
        raise NotImplementedError
    async def close(self) -> None:
        raise NotImplementedError
```

- [ ] **Step 1: Add failing EventBus contract tests**

Create `tests/messaging/test_event_bus_contract.py` with tests that start each
subscription as an `asyncio.Task`, wait on `asyncio.Event` readiness from the
handler instead of fixed sleeps, and always cancel tasks in `finally`:

```python
import asyncio
import pytest

from camera_ai.events import Event, InProcessEventBus


@pytest.mark.asyncio
async def test_topic_wildcards_and_independent_payload_copies():
    bus = InProcessEventBus()
    received: list[tuple[str, Event]] = []
    ready = asyncio.Event()

    async def first(event: Event) -> None:
        event.payload["consumer"] = "first"
        received.append(("first", event))
        if len(received) == 2: ready.set()

    async def second(event: Event) -> None:
        received.append(("second", event))
        if len(received) == 2: ready.set()

    tasks = [
        asyncio.create_task(bus.subscribe("detection.*", first)),
        asyncio.create_task(bus.subscribe("#", second)),
    ]
    try:
        await bus.wait_until_subscribed(2)
        await bus.publish(Event("detection.completed", "cam-01", {"count": 1}))
        await asyncio.wait_for(ready.wait(), 1)
        second_event = next(event for name, event in received if name == "second")
        assert "consumer" not in second_event.payload
    finally:
        for task in tasks: task.cancel()
        await bus.close()


@pytest.mark.asyncio
async def test_handler_retries_then_continues_with_next_event(caplog):
    bus = InProcessEventBus(max_attempts=2)
    attempts = 0
    completed = asyncio.Event()

    async def flaky(event: Event) -> None:
        nonlocal attempts
        attempts += 1
        if event.payload["id"] == 1:
            raise RuntimeError("broken")
        completed.set()

    task = asyncio.create_task(bus.subscribe("alert.created", flaky, subscriber_id="store"))
    try:
        await bus.wait_until_subscribed(1)
        await bus.publish(Event("alert.created", "cam", {"id": 1}))
        await bus.publish(Event("alert.created", "cam", {"id": 2}))
        await asyncio.wait_for(completed.wait(), 1)
        assert attempts == 3
        assert "broken" in caplog.text
    finally:
        task.cancel()
        await bus.close()
```

Also add a bounded queue test using `max_queue_size=1` and a blocked handler;
assert the third `publish` task remains pending until the handler is released.

- [ ] **Step 2: Run the new tests to verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/messaging/test_event_bus_contract.py -q
```

Expected: failure because wildcard matching, bounded queues, copy isolation,
subscriber IDs, and `close()` are absent.

- [ ] **Step 3: Implement Event and in-process pub/sub contract**

Move `Event`, its `JSONMessageCodec[Event]` mapper, `EventHandler`, and the abstract
`EventBus` into `src/camera_ai/messaging/event_bus.py`. Put only the asyncio
implementation in `src/camera_ai/messaging/inprocess.py`. Keep
`src/camera_ai/events.py` as a re-export facade so all existing imports work and
the new modules do not form a circular import.

Extend `Event.__init__` without changing existing positional arguments:

```python
def __init__(self, type: str, camera_id: str, payload: dict[str, JSONValue],
             source: str = "unknown", *, message_id: str | None = None,
             timestamp: float | None = None, correlation_id: str | None = None,
             causation_id: str | None = None, schema_version: int = 1) -> None:
```

Implement `topic_matches` by splitting pattern and event type on `.`: `*` matches
one segment and `#` matches the remaining segments. Store `_Subscription` records
containing pattern, subscriber ID, and a bounded private queue. `publish` must
encode once and decode separately for each matching subscriber, guaranteeing both
JSON validation and independent payload copies.

Use a `_QueuedEvent(event, attempt)` wrapper. Handler failure with attempt below
`max_attempts` goes back to the same private queue; exhaustion is logged with
message ID and subscriber ID, then processing continues. `close()` is idempotent,
marks the bus closed, and cancels/clears registered subscriptions. Provide
`wait_until_subscribed(count, timeout=1.0)` as a test/lifecycle helper.

- [ ] **Step 4: Run legacy and contract tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_events.py tests/test_alert_store.py tests/messaging/test_event_bus_contract.py -q
```

Expected: all tests pass without modifying call sites in `alert_store.py` or API.

- [ ] **Step 5: Commit**

```powershell
git add src/camera_ai/events.py src/camera_ai/messaging/event_bus.py src/camera_ai/messaging/inprocess.py tests/test_events.py tests/messaging/test_event_bus_contract.py
git commit -m "feat: harden in-process event bus contract"
```

---

### Task 3: Generic In-Process TaskQueue and VLMWorker Delivery Lifecycle

**Files:**
- Modify: `src/camera_ai/messaging/inprocess.py`
- Modify: `src/camera_ai/queue.py:31-216`
- Modify: `tests/test_queue.py`
- Create: `tests/messaging/test_task_queue_contract.py`

**Interfaces:**
- Consumes: `Delivery[T]`, `TaskQueue[T]`, `RejectMessageError` from Task 1.
- Produces: `InProcessTaskQueue[T]`, backward-compatible `VLMQueue`, and `VLMWorker` that ACKs only after required stores complete.

- [ ] **Step 1: Write failing generic queue contract tests**

Create `tests/messaging/test_task_queue_contract.py`:

```python
import pytest
from camera_ai.messaging.inprocess import InProcessTaskQueue


@pytest.mark.asyncio
async def test_priority_ack_retry_and_reject():
    queue = InProcessTaskQueue[str](max_attempts=2)
    await queue.enqueue("low", priority=4)
    await queue.enqueue("high", priority=1)

    high = await queue.receive()
    assert high.task == "high"
    await high.retry()
    retried = await queue.receive()
    assert retried.task == "high"
    assert retried.attempt == 1
    await retried.ack()

    low = await queue.receive()
    assert low.task == "low"
    await low.reject()
    assert queue.depth == 0
    assert queue.dead_letters == ("low",)


@pytest.mark.asyncio
async def test_retry_exhaustion_moves_task_to_dead_letters():
    queue = InProcessTaskQueue[str](max_attempts=1)
    await queue.enqueue("bad", priority=2)
    first = await queue.receive()
    await first.retry()
    second = await queue.receive()
    await second.retry()
    assert queue.dead_letters == ("bad",)
```

- [ ] **Step 2: Update VLMWorker tests before implementation**

Modify `tests/test_queue.py` so worker success asserts delivery ACK indirectly via
`queue.depth == 0`. Replace the existing error test with two deterministic tests:

```python
@pytest.mark.asyncio
async def test_worker_retries_transient_pipeline_error_then_succeeds():
    queue = VLMQueue(max_attempts=2)
    pipeline = MagicMock()
    pipeline.analyze_vlm = MagicMock(side_effect=[RuntimeError("temporary"), analysis])
    worker = VLMWorker(queue=queue, pipeline=pipeline,
                       alert_store=mock_alert_store, event_bus=AsyncMock())
    await worker.start()
    await queue.enqueue(_make_task("retry-success"))
    await asyncio.wait_for(_wait_until(lambda: pipeline.analyze_vlm.call_count == 2), 1)
    await worker.stop()
    mock_alert_store.update_vlm.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_rejects_permanent_message_error():
    queue = VLMQueue(max_attempts=2)
    pipeline = MagicMock()
    pipeline.analyze_vlm = MagicMock(side_effect=RejectMessageError("invalid task"))
    worker = VLMWorker(queue=queue, pipeline=pipeline,
                       alert_store=mock_alert_store, event_bus=AsyncMock())
    await worker.start()
    await queue.enqueue(_make_task("reject"))
    await asyncio.wait_for(_wait_until(lambda: len(queue.dead_letters) == 1), 1)
    await worker.stop()
    assert queue.dead_letters[0].task_id == "reject"
```

Add `_wait_until(predicate)` as an async polling helper with a 10 ms interval;
do not add fixed 100 ms sleeps to new tests.

- [ ] **Step 3: Run focused tests to verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/messaging/test_task_queue_contract.py tests/test_queue.py -q
```

Expected: failures because generic deliveries and worker ACK/retry/reject are absent.

- [ ] **Step 4: Implement generic in-process task delivery**

In `messaging/inprocess.py`, add an orderable internal record with fields
`priority`, monotonic sequence number, task, and attempt. The constructor is
`InProcessTaskQueue(max_queue_size=1024, max_attempts=3,
priority_resolver: Callable[[T, int], int] | None = None)`; immediately before
selecting a delivery, drain the currently ready records under an `asyncio.Lock`,
recompute their priorities, balance every `get_nowait()` with `task_done()`, then
put them back. `receive()` returns a `Delivery` whose callbacks:

- `ack`: calls `asyncio.PriorityQueue.task_done()`.
- `retry`: calls `task_done()`, then re-enqueues with `attempt + 1`; after the
  configured maximum it appends the task to `_dead_letters` instead.
- `reject`: calls `task_done()` and appends the task to `_dead_letters`.

Expose `dead_letters` as an immutable tuple and make `close()` idempotent.

Refactor `VLMQueue` into a thin subclass of `InProcessTaskQueue[VLMTask]` and pass
`priority_resolver=lambda task, _queued: task.effective_priority()` to `super()`.
This preserves the current 30/60/120-second aging policy without putting VLM
knowledge into the generic queue:

```python
class VLMQueue(InProcessTaskQueue[VLMTask]):
    def __init__(self, *, max_queue_size: int = 1024, max_attempts: int = 3) -> None:
        super().__init__(
            max_queue_size=max_queue_size,
            max_attempts=max_attempts,
            priority_resolver=lambda task, _queued: task.effective_priority(),
        )

    async def enqueue(self, task: VLMTask, *, priority: int | None = None) -> None:
        await super().enqueue(task, priority=task.priority if priority is None else priority)

    async def dequeue(self) -> VLMTask:
        delivery = await self.receive()
        await delivery.ack()
        return delivery.task
```

Keep `effective_priority()` and existing direct dequeue tests for backward
compatibility. Add one test with two tasks proving an old low-priority task is
selected before a newly enqueued lower-urgency task after the resolver refresh.

Refactor `VLMWorker.run()` to retain `delivery: Delivery[VLMTask] | None` each
iteration and type its constructor dependency as `TaskQueue[VLMTask]` rather than
the concrete `VLMQueue`. On successful pipeline and store updates, call
`delivery.ack()`. Catch
`RejectMessageError` and call `delivery.reject()`. For other exceptions call
`delivery.retry()`. Never ACK before `AlertStore` and `AnalysisStore` writes finish.

- [ ] **Step 5: Run queue, worker, and async regression tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/messaging/test_task_queue_contract.py tests/test_queue.py tests/test_async_pipeline.py -q
```

Expected: all tests pass and the current API-facing VLM behavior remains in-process.

- [ ] **Step 6: Commit**

```powershell
git add src/camera_ai/messaging/inprocess.py src/camera_ai/queue.py tests/test_queue.py tests/messaging/test_task_queue_contract.py
git commit -m "feat: add task delivery lifecycle to in-process VLM queue"
```

---

### Task 4: Optional RabbitMQ Settings and Robust Connection

**Files:**
- Modify: `pyproject.toml`
- Create: `src/camera_ai/messaging/config.py`
- Create: `src/camera_ai/messaging/rabbit_connection.py`
- Create: `tests/messaging/test_rabbit_config.py`
- Create: `tests/messaging/test_rabbit_connection.py`

**Interfaces:**
- Consumes: `BackendUnavailableError` from Task 1.
- Produces: `RabbitMQSettings.from_env()`, `MessagingSettings.from_env()`, `RabbitMQConnection.channel(prefetch_count=None)`, `RabbitMQConnection.health()`, `RabbitMQConnection.close()`.

- [ ] **Step 1: Add optional dependency and failing settings tests**

Add to `pyproject.toml`:

```toml
[project.optional-dependencies]
rabbitmq = ["aio-pika>=9.5,<10"]
```

Create tests asserting defaults and secret-safe representation:

```python
def test_rabbit_settings_defaults_and_redacted_repr(monkeypatch):
    monkeypatch.setenv("RABBITMQ_URL", "amqp://user:secret@broker/vhost")
    settings = RabbitMQSettings.from_env()
    assert settings.event_exchange == "camera_ai.events"
    assert settings.task_exchange == "camera_ai.tasks"
    assert settings.prefetch_vlm == 1
    assert "secret" not in repr(settings)


def test_invalid_backend_is_rejected(monkeypatch):
    monkeypatch.setenv("CAMERA_AI_EVENT_BUS_BACKEND", "kafka")
    with pytest.raises(ValueError, match="inprocess|rabbitmq"):
        MessagingSettings.from_env()
```

- [ ] **Step 2: Write lazy-import connection tests with an injected connector**

Use a fake async connector and fake connection/channel objects. Assert concurrent
`channel()` calls invoke connector exactly once, publisher confirms and
`on_return_raises=True` are requested, QoS is applied when non-null, and two
`close()` calls close the connection once. Also patch Python import to raise
`ModuleNotFoundError("aio_pika")` and assert the default connector raises
`BackendUnavailableError` mentioning `pip install -e .[rabbitmq]` without exposing
the Rabbit URL. Assert `health()` returns `False` before a connection exists,
`True` for an open robust connection, and `False` after close; it must not create
a connection merely to answer health.

- [ ] **Step 3: Run tests to verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/messaging/test_rabbit_config.py tests/messaging/test_rabbit_connection.py -q
```

Expected: modules do not exist.

- [ ] **Step 4: Implement configuration and connection lifecycle**

Define `RabbitMQSettings` as a frozen dataclass with these broker defaults:

```python
url: str = "amqp://guest:guest@localhost/"
event_exchange: str = "camera_ai.events"
task_exchange: str = "camera_ai.tasks"
dead_exchange: str = "camera_ai.dead"
prefetch_events: int = 32
prefetch_vlm: int = 1
max_priority: int = 5
max_attempts: int = 3
retry_delay_ms: int = 5000
```

Define the top-level frozen configuration separately so adapter constructors only
receive broker settings:

```python
@dataclass(frozen=True)
class MessagingSettings:
    event_bus_backend: Literal["inprocess", "rabbitmq"] = "inprocess"
    vlm_queue_backend: Literal["inprocess", "rabbitmq"] = "inprocess"
    rabbitmq: RabbitMQSettings = field(default_factory=RabbitMQSettings)
```

Both `from_env()` methods validate positive prefetch/attempt/delay values and
accepted backend names. URL redaction belongs to `RabbitMQSettings.__repr__`.

`RabbitMQConnection` accepts an optional connector callable for unit tests. The
default connector imports `aio_pika` inside the callable, uses
`aio_pika.connect_robust(url)`, and is guarded by an `asyncio.Lock`. Each
`channel()` call opens a fresh robust channel with publisher confirms and return
errors enabled; apply `set_qos(prefetch_count=...)` only for consumer channels.

- [ ] **Step 5: Run focused tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/messaging/test_rabbit_config.py tests/messaging/test_rabbit_connection.py -q
git add pyproject.toml src/camera_ai/messaging/config.py src/camera_ai/messaging/rabbit_connection.py tests/messaging/test_rabbit_config.py tests/messaging/test_rabbit_connection.py
git commit -m "feat: add optional RabbitMQ connection infrastructure"
```

---

### Task 5: RabbitMQ EventBus Adapter

**Files:**
- Create: `src/camera_ai/messaging/rabbit_event_bus.py`
- Create: `tests/messaging/fakes_amqp.py`
- Create: `tests/messaging/test_rabbit_event_bus.py`

**Interfaces:**
- Consumes: `Event`, `EventBus`, `EventHandler`, `JSONMessageCodec`, `RabbitMQConnection`, `RabbitMQSettings`.
- Produces: `RabbitMQEventBus(connection, settings, *, message_factory=None, owns_connection=False)` and `publish/subscribe/close` matching the EventBus interface. `message_factory` has signature `Callable[..., AMQPMessage]` and defaults to a lazy `aio_pika.Message` factory.

- [ ] **Step 1: Build reusable fake AMQP boundary**

Create deterministic fake exchange, queue, incoming-message, iterator, channel,
and connection classes in `tests/messaging/fakes_amqp.py`. Record declarations,
bindings, published message properties, ACK/reject calls, and QoS. The fake must
support injecting incoming messages without running RabbitMQ.

- [ ] **Step 2: Write failing topology and publish tests**

Test these exact outcomes:

```python
await bus.publish(Event("detection.completed", "cam-01", {"count": 2}, source="yolo"))
published = fake.events_exchange.published[0]
assert published.routing_key == "detection.completed"
assert published.mandatory is True
assert published.delivery_mode == 2
assert published.content_type == "application/json"
assert published.message_id
```

Start `subscribe("detection.*", handler, subscriber_id="rule-engine")` and assert:

- topic exchange `camera_ai.events` is durable;
- queue `camera_ai.events.rule-engine` is durable/non-exclusive;
- binding key is `detection.*`;
- queue DLX points to `camera_ai.dead` with subscriber-specific routing key;
- successful handler ACKs exactly once;
- malformed JSON rejects without requeue;
- a handler `RejectMessageError` rejects without requeue;
- generic handler error publishes an incremented-attempt copy to
  `camera_ai.events.rule-engine.retry`, then ACKs original;
- max attempt rejects original into its subscriber DLQ.

- [ ] **Step 3: Run adapter tests to verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/messaging/test_rabbit_event_bus.py -q
```

Expected: adapter module does not exist.

- [ ] **Step 4: Implement subscriber-local topology and delivery handling**

Use lazy `aio_pika` imports only in the default `message_factory`; unit tests
inject the fake message factory, so importing and exercising the adapter does not
require the optional package. For a stable subscriber declare:

```text
main:  camera_ai.events.<subscriber_id>
retry: camera_ai.events.<subscriber_id>.retry
dead:  camera_ai.events.<subscriber_id>.dlq
```

The retry queue has `x-message-ttl=retry_delay_ms`, dead-letters through the
default exchange back to the main queue name, and is never bound to the topic
exchange. This ensures retry is local to the failing subscriber. The main queue
dead-letters to `camera_ai.dead`; bind the dead queue using the subscriber-specific
dead routing key.

When `subscriber_id=None`, create a generated exclusive auto-delete main queue and
matching auto-delete retry/dead queues. Sanitize explicit IDs to lowercase ASCII
letters, digits, dash, underscore, and dot; reject an empty result.

On retry, publish the original body with `x-attempt + 1` and original identifiers
to the retry queue using publisher confirms; ACK the original only after retry
publish succeeds. `close()` cancels iterators/channels owned by this adapter but
does not close a shared `RabbitMQConnection` unless `owns_connection=True`.
Every handler failure log includes message ID, event type, subscriber ID, attempt,
and exception traceback; never include the broker URL or payload body in that log.

- [ ] **Step 5: Run EventBus adapter and shared contract tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/messaging/test_rabbit_event_bus.py tests/messaging/test_event_bus_contract.py tests/test_events.py -q
```

Expected: all tests pass; no live broker or optional package is required because
the Rabbit boundary is injected.

- [ ] **Step 6: Commit**

```powershell
git add src/camera_ai/messaging/rabbit_event_bus.py tests/messaging/fakes_amqp.py tests/messaging/test_rabbit_event_bus.py
git commit -m "feat: add RabbitMQ event bus adapter"
```

---

### Task 6: RabbitMQ Generic TaskQueue Adapter

**Files:**
- Create: `src/camera_ai/messaging/rabbit_task_queue.py`
- Create: `tests/messaging/test_rabbit_task_queue.py`

**Interfaces:**
- Consumes: `TaskQueue[T]`, `Delivery[T]`, `MessageCodec[T]`, `RabbitMQConnection`, `RabbitMQSettings`, fake AMQP objects from Task 5.
- Produces: `RabbitMQTaskQueue[T](name, codec, connection, settings, *, message_factory=None, owns_connection=False)` with durable priority queue and manual delivery lifecycle. The factory signature is identical to Task 5's message factory.

- [ ] **Step 1: Write failing queue declaration and enqueue tests**

Use `DemoTask` plus `JSONMessageCodec[DemoTask]`. Assert queue `camera_ai.tasks.vlm`
is durable with:

```python
{
    "x-max-priority": 5,
    "x-dead-letter-exchange": "camera_ai.dead",
    "x-dead-letter-routing-key": "task.vlm.dead",
}
```

Assert task exchange is direct/durable, routing key is `vlm`, messages are
persistent and mandatory, and domain priority maps exactly:

```python
assert map_priority(1, max_priority=5) == 5
assert map_priority(3, max_priority=5) == 3
assert map_priority(5, max_priority=5) == 1
```

Values outside `1..max_priority` must raise `ValueError` before publish.

- [ ] **Step 2: Write failing receive lifecycle tests**

Inject one encoded message and assert `receive()` returns the decoded task and
attempt header. Verify:

- `delivery.ack()` ACKs the incoming message;
- `delivery.retry()` publishes to `camera_ai.tasks.vlm.retry` with attempt+1,
  preserving message ID, correlation ID, body and priority, then ACKs original;
- retry publish failure does not ACK original;
- retry beyond maximum rejects without requeue, reaching DLQ;
- `delivery.reject()` rejects without requeue;
- malformed payload rejects without requeue and `receive()` continues to the next
  valid message rather than returning an invalid delivery;
- consumer channel QoS is `prefetch_vlm == 1`.

- [ ] **Step 3: Run focused tests to verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/messaging/test_rabbit_task_queue.py -q
```

Expected: adapter does not exist.

- [ ] **Step 4: Implement the durable priority work queue**

Declare:

```text
exchange: camera_ai.tasks (direct, durable)
main:     camera_ai.tasks.<name>
retry:    camera_ai.tasks.<name>.retry
dead:     camera_ai.tasks.<name>.dlq
```

Retry queue TTL routes through the default exchange back to the main queue.
Dead queue binds to `camera_ai.dead` using `task.<name>.dead`. Maintain one lazy
queue iterator per adapter instance; `receive()` loops over invalid messages until
it can return a valid `Delivery[T]`.

The generic adapter must know nothing about `VLMTask`, frames, Pydantic, or Qwen.
It accepts an injected `MessageCodec[T]`. Expose broker depth only when passive
declaration data is available; before initialization return zero. Do not issue a
network call from the synchronous `depth` property.

- [ ] **Step 5: Run adapter tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/messaging/test_rabbit_task_queue.py tests/messaging/test_task_queue_contract.py -q
git add src/camera_ai/messaging/rabbit_task_queue.py tests/messaging/test_rabbit_task_queue.py
git commit -m "feat: add RabbitMQ priority task queue adapter"
```

---

### Task 7: Factories, Compatibility Exports, and Default-Runtime Safety

**Files:**
- Create: `src/camera_ai/messaging/factory.py`
- Modify: `src/camera_ai/messaging/__init__.py`
- Modify: `src/camera_ai/events.py`
- Modify: `.env.example`
- Create: `tests/messaging/test_factory.py`
- Create: `tests/test_default_messaging_runtime.py`

**Interfaces:**
- Consumes: both backend implementations and settings from Tasks 2–6.
- Produces: `build_event_bus(settings: MessagingSettings) -> EventBus` and `build_task_queue(name: str, settings: MessagingSettings, *, codec: MessageCodec[T] | None = None) -> TaskQueue[T]`; does not modify API composition.

- [ ] **Step 1: Write failing factory tests**

Test:

```python
def test_default_factory_builds_inprocess_without_importing_aio_pika(monkeypatch):
    settings = MessagingSettings(event_bus_backend="inprocess", vlm_queue_backend="inprocess")
    monkeypatch.setitem(sys.modules, "aio_pika", None)
    assert isinstance(build_event_bus(settings), InProcessEventBus)


def test_rabbit_event_factory_is_lazy():
    settings = MessagingSettings(event_bus_backend="rabbitmq")
    bus = build_event_bus(settings)
    assert isinstance(bus, RabbitMQEventBus)
    # Construction does not establish a socket; first publish/subscribe does.
```

For the task factory, require an explicit codec for RabbitMQ. Assert requesting
`vlm_queue_backend="rabbitmq"` without a codec raises a message explaining that
`VLMTask` is not transport-safe and `FrameStore/VLMJob` are required.

- [ ] **Step 2: Add startup safety regression test**

Create `tests/test_default_messaging_runtime.py`. Patch
`RabbitMQConnection._connect` to raise if called, import `apps.api.main`, and assert:

```python
assert isinstance(main.event_bus, InProcessEventBus)
assert isinstance(main.vlm_queue, VLMQueue)
```

This test proves the dormant Rabbit code cannot alter current startup.

- [ ] **Step 3: Run tests to verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/messaging/test_factory.py tests/test_default_messaging_runtime.py -q
```

Expected: factory is absent.

- [ ] **Step 4: Implement factories and exports**

`build_event_bus` returns an in-process bus or constructs a Rabbit adapter with a
shared/lazy connection. `build_task_queue` returns a generic in-process queue by
default; Rabbit selection requires `name` and codec. Construction never opens a
socket.

Keep these legacy imports working:

```python
from camera_ai.events import Event, EventBus, InProcessEventBus
from camera_ai.queue import VLMTask, VLMQueue, VLMWorker
```

Add commented dormant configuration to `.env.example`:

```dotenv
# RabbitMQ adapters are implemented but are not wired into apps/api/main.py yet.
# CAMERA_AI_EVENT_BUS_BACKEND=inprocess
# CAMERA_AI_VLM_QUEUE_BACKEND=inprocess
# RABBITMQ_URL=amqp://guest:guest@localhost/
```

Do not read these values in `apps/api/main.py` in this task.

- [ ] **Step 5: Run compatibility tests and commit**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/messaging/test_factory.py tests/test_default_messaging_runtime.py tests/test_events.py tests/test_queue.py tests/test_async_pipeline.py -q
git add src/camera_ai/messaging src/camera_ai/events.py .env.example tests/messaging/test_factory.py tests/test_default_messaging_runtime.py
git commit -m "feat: add dormant messaging backend factories"
```

---

### Task 8: Opt-In RabbitMQ Integration Tests and Documentation

**Files:**
- Create: `tests/integration/test_rabbitmq_messaging.py`
- Modify: `README.md`
- Modify: `docs/processing-plane-architecture.md:395-445`
- Modify: `docs/camera-ai-system-architecture.md:422-475`

**Interfaces:**
- Consumes: public messaging API from Tasks 1–7.
- Produces: broker-level verification gated by `RABBITMQ_TEST_URL` and accurate architecture status documentation.

- [ ] **Step 1: Write opt-in integration tests**

At module import:

```python
RABBIT_URL = os.getenv("RABBITMQ_TEST_URL")
pytestmark = pytest.mark.skipif(
    not RABBIT_URL,
    reason="set RABBITMQ_TEST_URL to run RabbitMQ integration tests",
)
```

Use UUID-suffixed subscriber/queue names. Test:

1. One event reaches two different subscriber IDs.
2. Two consumers sharing one task queue receive one task each, with no duplicate
   simultaneous delivery.
3. Closing a consumer before ACK causes eventual redelivery.
4. Rejecting a task makes it observable in the task DLQ.

Each test uses `asyncio.wait_for(..., 5)` and closes all adapters/connections in
`finally`. Add cleanup helpers that delete only the exact UUID-suffixed test
queues/exchanges created by that test; never use wildcard deletion.

- [ ] **Step 2: Verify integration tests skip by default**

```powershell
Remove-Item Env:RABBITMQ_TEST_URL -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -m pytest tests/integration/test_rabbitmq_messaging.py -q
```

Expected: four tests skipped and no connection attempt.

- [ ] **Step 3: Update documentation status and usage**

Document three explicit states:

```text
Current runtime: InProcessEventBus + VLMQueue
Available code: RabbitMQEventBus + generic RabbitMQTaskQueue
Blocked runtime switch: transport-safe VLMJob + FrameStore + shared AlertStore
```

Correct the EventBus interface example in processing-plane architecture: ACK is
owned by the subscription adapter/delivery lifecycle, not a public
`EventBus.ack(event)` method. Document topic exchange fan-out versus work-queue
competing consumers and warn against queue-per-camera/raw-frame messages.

Include optional setup commands without claiming RabbitMQ is active:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[rabbitmq]"
$env:RABBITMQ_TEST_URL = "amqp://guest:guest@localhost/"
.\.venv\Scripts\python.exe -m pytest tests/integration/test_rabbitmq_messaging.py -q
```

- [ ] **Step 4: Run base suite and optional-package unit suite**

First run the normal suite:

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

Expected: all existing and new unit/contract tests pass; Rabbit integration tests
skip when the environment variable is absent.

Then install only the Python client extra and rerun messaging tests:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[rabbitmq]"
.\.venv\Scripts\python.exe -m pytest tests/messaging tests/test_default_messaging_runtime.py -q
```

Expected: all pass without requiring a RabbitMQ server.

- [ ] **Step 5: Run broker integration only when explicitly supplied**

```powershell
if ($env:RABBITMQ_TEST_URL) {
    .\.venv\Scripts\python.exe -m pytest tests/integration/test_rabbitmq_messaging.py -q
}
```

Expected with a valid broker: all four integration tests pass. If no URL is set,
this step performs no network action and is recorded as not run, not failed.

- [ ] **Step 6: Commit final verification/docs**

```powershell
git add tests/integration/test_rabbitmq_messaging.py README.md docs/processing-plane-architecture.md docs/camera-ai-system-architecture.md
git commit -m "test: document and verify RabbitMQ transport adapters"
```

---

## Parallel Execution Boundaries

Task 1 is the shared contract gate. Tasks 2 and 3 may then run sequentially on the
in-process branch because both modify `messaging/inprocess.py`. Task 4 may run in
parallel with Tasks 2–3. After Task 4 is merged, Tasks 5 and 6 are independent and
may run in parallel. Task 7 waits for Tasks 2, 3, 5, and 6. Task 8 runs last.

```text
Task 1 ──┬── Task 2 ── Task 3 ──────────┐
         └── Task 4 ──┬── Task 5 ───────┼── Task 7 ── Task 8
                      └── Task 6 ───────┘
```

## Final Acceptance Checklist

- [ ] Base environment imports and starts API without `aio-pika` or RabbitMQ.
- [ ] Existing EventBus and VLMQueue call sites remain source-compatible.
- [ ] In-process events fan out with isolated payload copies and bounded queues.
- [ ] In-process tasks support priority plus ACK/retry/reject semantics.
- [ ] Rabbit event retries are subscriber-local and do not duplicate successful subscribers.
- [ ] Rabbit task messages are persistent, priority-mapped, manual-ACKed, and DLQ-bound.
- [ ] Publisher confirms and mandatory routing are enabled.
- [ ] Serialization rejects bytes, ndarray, NaN, arbitrary objects, and unsupported schema versions.
- [ ] Rabbit VLM backend cannot be selected without an explicit transport-safe codec.
- [ ] Integration tests are skipped unless `RABBITMQ_TEST_URL` is explicitly set.
- [ ] Full regression suite passes with the default in-process runtime.
