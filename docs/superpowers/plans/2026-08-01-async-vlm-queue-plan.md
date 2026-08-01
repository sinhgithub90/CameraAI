# Async VLM Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tách Detection & VLM thành 2 đường bất đồng bộ — detect trả về ngay (<1s), VLM chạy nền qua priority queue, client poll kết quả.

**Architecture:** In-process async queue với DI pattern. `EventBus` (pub/sub broadcast) và `VLMQueue` (priority work queue) là 2 thứ riêng biệt. `SecurityAIPipeline` được tách thành `detect()` + `analyze_vlm()`. API cũ giữ nguyên backward compat, API mới thêm `/async/*` + `GET /alerts/{id}`. Tất cả in-process, 0 dependency ngoài.

**Tech Stack:** asyncio.Queue, asyncio.PriorityQueue, FastAPI, Pydantic

## Global Constraints

- Python 3.13+, không dependency ngoài cho queue/event bus ở Phase 1
- Backward compat: `POST /analyze/image` và `POST /analyze/video` giữ nguyên
- Mọi interface dùng ABC để swap implementation sau này (DI)
- TDD: test viết trước, code sau
- Commit thường xuyên sau mỗi task

---

## File Structure

| File | Trách nhiệm |
|---|---|
| `src/camera_ai/events.py` | **Mới** — `Event`, `EventBus` (ABC), `InProcessEventBus` |
| `src/camera_ai/queue.py` | **Mới** — `VLMTask` (dataclass), `VLMQueue`, `VLMWorker` |
| `src/camera_ai/alert_store.py` | **Mới** — `Alert` (model), `AlertStore` (ABC), `InMemoryAlertStore` |
| `src/camera_ai/schemas.py` | **Sửa** — thêm `status: str` vào `VLMResult` |
| `src/camera_ai/pipeline.py` | **Sửa** — tách `detect()`, `analyze_vlm()` từ `analyze_event()` |
| `apps/api/main.py` | **Sửa** — thêm `/async/*`, `GET /alerts/{id}`, startup VLMWorker |
| `tests/test_events.py` | **Mới** — test EventBus |
| `tests/test_queue.py` | **Mới** — test VLMQueue + VLMWorker |
| `tests/test_alert_store.py` | **Mới** — test AlertStore |
| `tests/test_async_pipeline.py` | **Mới** — test async flow end-to-end |

---

### Task 1: Event + EventBus Interface + InProcessEventBus

**Files:**
- Create: `src/camera_ai/events.py`
- Create: `tests/test_events.py`

**Interfaces:**
- Produces: `Event`, `EventBus` (ABC), `InProcessEventBus` — dùng cho Task 5 (AlertStore), Task 6 (pipeline), Task 7 (API startup)

- [ ] **Step 1: Write the test file**

```python
# tests/test_events.py
"""Tests for EventBus interface and InProcessEventBus."""
import asyncio

import pytest

from camera_ai.events import Event, EventBus, InProcessEventBus


class TestEvent:
    def test_event_creation(self):
        event = Event(
            type="alert.created",
            source="rule_engine",
            camera_id="cam_01",
            payload={"alert_id": "abc", "level": "high"},
        )
        assert event.type == "alert.created"
        assert event.source == "rule_engine"
        assert event.camera_id == "cam_01"
        assert event.payload["alert_id"] == "abc"
        assert event.timestamp > 0

    def test_event_defaults(self):
        event = Event(type="frame.captured", camera_id="cam_01", payload={})
        assert event.source == "unknown"


class TestInProcessEventBus:
    @pytest.fixture
    def bus(self):
        return InProcessEventBus()

    @pytest.mark.asyncio
    async def test_publish_subscribe_single(self, bus):
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        # subscribe chạy trong background task
        task = asyncio.create_task(bus.subscribe("test.type", handler))
        await asyncio.sleep(0.01)  # cho subscriber ready

        event = Event(type="test.type", camera_id="cam_01", payload={"x": 1})
        await bus.publish(event)
        await asyncio.sleep(0.05)  # cho handler xử lý

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert len(received) == 1
        assert received[0].type == "test.type"
        assert received[0].payload == {"x": 1}

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self, bus):
        received_1: list[Event] = []
        received_2: list[Event] = []

        async def handler_1(event: Event) -> None:
            received_1.append(event)

        async def handler_2(event: Event) -> None:
            received_2.append(event)

        t1 = asyncio.create_task(bus.subscribe("alert.created", handler_1))
        t2 = asyncio.create_task(bus.subscribe("alert.created", handler_2))
        await asyncio.sleep(0.01)

        event = Event(type="alert.created", camera_id="cam_01", payload={})
        await bus.publish(event)
        await asyncio.sleep(0.05)

        t1.cancel()
        t2.cancel()

        assert len(received_1) == 1
        assert len(received_2) == 1  # cả 2 cùng nhận

    @pytest.mark.asyncio
    async def test_different_types_routed_correctly(self, bus):
        alerts: list[Event] = []
        detections: list[Event] = []

        async def alert_handler(event: Event) -> None:
            alerts.append(event)

        async def detection_handler(event: Event) -> None:
            detections.append(event)

        t1 = asyncio.create_task(bus.subscribe("alert.created", alert_handler))
        t2 = asyncio.create_task(bus.subscribe("detection.completed", detection_handler))
        await asyncio.sleep(0.01)

        await bus.publish(Event(type="alert.created", camera_id="cam_01", payload={}))
        await asyncio.sleep(0.05)

        t1.cancel()
        t2.cancel()

        assert len(alerts) == 1
        assert len(detections) == 0  # không nhận sai loại

    @pytest.mark.asyncio
    async def test_subscribe_non_existent_type_no_error(self, bus):
        """Subscriber vẫn chạy bình thường khi không có message."""
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        task = asyncio.create_task(bus.subscribe("rare.event", handler))
        await asyncio.sleep(0.02)
        task.cancel()
        assert len(received) == 0


class TestEventBusInterface:
    """Verify EventBus ABC cannot be instantiated."""
    def test_eventbus_is_abstract(self):
        with pytest.raises(TypeError):
            EventBus()  # type: ignore
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_events.py -v
```
Expected: FAIL — module `camera_ai.events` not found

- [ ] **Step 3: Write implementation**

```python
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
    """asyncio.Queue-based event bus for single-process deployments."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[Event]] = defaultdict(asyncio.Queue)

    async def publish(self, event: Event) -> None:
        await self._queues[event.type].put(event)

    async def subscribe(self, event_type: str, handler: EventHandler) -> None:
        queue = self._queues[event_type]
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_events.py -v
```
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/camera_ai/events.py tests/test_events.py
git commit -m "feat: add EventBus interface and InProcessEventBus"
```

---

### Task 2: VLMResult.status Schema Change

**Files:**
- Modify: `src/camera_ai/schemas.py` — thêm `status` field vào `VLMResult`

**Interfaces:**
- Produces: `VLMResult.status: str` — dùng cho Task 3 (Alert), Task 5 (AlertStore), Task 6 (pipeline)

- [ ] **Step 1: Write test (dùng test hiện có để verify)**

```bash
python -m pytest tests/test_pipeline.py -v
```
Expected: 8 passed (chưa thay đổi gì, verify baseline)

- [ ] **Step 2: Add `status` field to VLMResult**

```python
# src/camera_ai/schemas.py — sửa VLMResult
class VLMResult(BaseModel):
    """VLM portion of the pipeline output."""

    summary: str
    observations: list[str] = Field(default_factory=list)
    degraded: bool = False
    skipped: bool = Field(
        default=False,
        description="True when the gate skipped the VLM (no cheap trigger fired).",
    )
    status: str = Field(
        default="completed",
        description="'pending' | 'completed' | 'skipped' — lifecycle state.",
    )
```

- [ ] **Step 3: Verify test tiếp tục pass (backward compat)**

```bash
python -m pytest tests/test_pipeline.py -v
```
Expected: 8 passed — trường mới có default, code cũ không bị vỡ

- [ ] **Step 4: Commit**

```bash
git add src/camera_ai/schemas.py
git commit -m "feat: add VLMResult.status field for async pipeline lifecycle"
```

---

### Task 3: Alert Model + AlertStore Interface + InMemoryAlertStore

**Files:**
- Create: `src/camera_ai/alert_store.py`
- Create: `tests/test_alert_store.py`

**Interfaces:**
- Consumes: `VLMResult` (Task 2), `EventBus` (Task 1)
- Produces: `Alert`, `AlertStore` (ABC), `InMemoryAlertStore` — dùng cho Task 5 (VLMWorker), Task 7 (API)

- [ ] **Step 1: Write the test file**

```python
# tests/test_alert_store.py
"""Tests for AlertStore interface and InMemoryAlertStore."""
import asyncio

import pytest

from camera_ai.alert_store import Alert, AlertStore, InMemoryAlertStore
from camera_ai.events import Event, InProcessEventBus
from camera_ai.schemas import AlertLevel, SceneAnalysis, SecurityDecision, VLMResult


def _make_alert(
    alert_id: str = "test-001",
    camera_id: str = "cam_01",
    status: str = "pending",
) -> Alert:
    return Alert(
        id=alert_id,
        camera_id=camera_id,
        rule_id="test_rule",
        vlm=VLMResult(summary="", status=status),
        security=SecurityDecision(alert_level=AlertLevel.MEDIUM),
    )


class TestAlert:
    def test_alert_creation_defaults(self):
        alert = Alert(id="a1", camera_id="cam_01")
        assert alert.id == "a1"
        assert alert.camera_id == "cam_01"
        assert alert.vlm.status == "pending"
        assert alert.security.alert_level == AlertLevel.LOW

    def test_alert_update_vlm(self):
        alert = _make_alert(status="pending")
        analysis = SceneAnalysis(
            summary="Có xâm nhập",
            alert_level=AlertLevel.HIGH,
            observations=["người trèo rào"],
            risks=["xam_nhap"],
            recommended_action="goi_bao_ve",
        )
        alert.update_vlm(analysis)
        assert alert.vlm.status == "completed"
        assert alert.vlm.summary == "Có xâm nhập"
        assert alert.security.alert_level == AlertLevel.HIGH


class TestInMemoryAlertStore:
    @pytest.fixture
    def store(self):
        bus = InProcessEventBus()
        return InMemoryAlertStore(event_bus=bus)

    @pytest.mark.asyncio
    async def test_create_and_get(self, store):
        alert = _make_alert(alert_id="a1")
        await store.create(alert)
        retrieved = await store.get("a1")
        assert retrieved is not None
        assert retrieved.id == "a1"
        assert retrieved.vlm.status == "pending"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, store):
        assert await store.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_update_vlm(self, store):
        alert = _make_alert(alert_id="a2", status="pending")
        await store.create(alert)

        analysis = SceneAnalysis(
            summary="Phân tích xong",
            alert_level=AlertLevel.HIGH,
        )
        await store.update_vlm("a2", analysis)

        updated = await store.get("a2")
        assert updated is not None
        assert updated.vlm.status == "completed"
        assert updated.security.alert_level == AlertLevel.HIGH

    @pytest.mark.asyncio
    async def test_list_active(self, store):
        await store.create(_make_alert(alert_id="a1", camera_id="cam_01", status="pending"))
        await store.create(_make_alert(alert_id="a2", camera_id="cam_01", status="completed"))
        await store.create(_make_alert(alert_id="a3", camera_id="cam_02", status="pending"))

        active = await store.list_active("cam_01")
        assert len(active) == 1
        assert active[0].id == "a1"

    @pytest.mark.asyncio
    async def test_publishes_event_on_create(self, store):
        received: list[Event] = []

        async def handler(event: Event) -> None:
            received.append(event)

        task = asyncio.create_task(
            store.event_bus.subscribe("alert.created", handler)
        )
        await asyncio.sleep(0.01)

        await store.create(_make_alert(alert_id="evt-1"))
        await asyncio.sleep(0.05)

        task.cancel()
        assert len(received) == 1
        assert received[0].type == "alert.created"
        assert received[0].payload["alert_id"] == "evt-1"


class TestAlertStoreInterface:
    def test_alert_store_is_abstract(self):
        with pytest.raises(TypeError):
            AlertStore()  # type: ignore
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_alert_store.py -v
```
Expected: FAIL — module not found

- [ ] **Step 3: Write implementation**

```python
# src/camera_ai/alert_store.py
"""Alert persistence — in-memory store for async pipeline results."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from .events import Event, EventBus
from .schemas import AlertLevel, SceneAnalysis, SecurityDecision, VLMResult


class Alert:
    """One alert in the async pipeline lifecycle."""

    def __init__(
        self,
        id: str,
        camera_id: str,
        rule_id: str = "default",
        vlm: VLMResult | None = None,
        security: SecurityDecision | None = None,
        created_at: datetime | None = None,
    ) -> None:
        self.id = id
        self.camera_id = camera_id
        self.rule_id = rule_id
        self.vlm = vlm or VLMResult(summary="", status="pending")
        self.security = security or SecurityDecision(alert_level=AlertLevel.LOW)
        self.created_at = created_at or datetime.now()

    def update_vlm(self, analysis: SceneAnalysis) -> None:
        """Apply VLM analysis to this alert."""
        self.vlm = VLMResult(
            summary=analysis.summary,
            observations=analysis.observations,
            degraded=analysis.degraded,
            status="completed",
        )
        self.security = SecurityDecision(
            alert_level=analysis.alert_level,
            risks=analysis.risks,
            recommended_action=analysis.recommended_action,
        )

    def to_dict(self) -> dict:
        """Serialize for API response."""
        return {
            "id": self.id,
            "camera_id": self.camera_id,
            "rule_id": self.rule_id,
            "vlm": self.vlm.model_dump(),
            "security": self.security.model_dump(),
            "created_at": self.created_at.isoformat(),
        }


class AlertStore(ABC):
    """Abstract alert persistence. Swap InMemoryAlertStore → PostgreSQLAlertStore."""

    @abstractmethod
    async def create(self, alert: Alert) -> None:
        """Persist a new alert."""
        ...

    @abstractmethod
    async def get(self, alert_id: str) -> Alert | None:
        """Retrieve by ID."""
        ...

    @abstractmethod
    async def update_vlm(self, alert_id: str, analysis: SceneAnalysis) -> None:
        """Apply VLM analysis result to an existing alert."""
        ...

    @abstractmethod
    async def list_active(self, camera_id: str) -> list[Alert]:
        """List alerts with vlm.status == 'pending' for a camera."""
        ...


class InMemoryAlertStore(AlertStore):
    """Dict-backed store. Alerts lost on restart — acceptable for Phase 1."""

    def __init__(self, event_bus: EventBus) -> None:
        self._alerts: dict[str, Alert] = {}
        self.event_bus = event_bus

    async def create(self, alert: Alert) -> None:
        self._alerts[alert.id] = alert
        await self.event_bus.publish(
            Event(
                type="alert.created",
                source="alert_store",
                camera_id=alert.camera_id,
                payload={
                    "alert_id": alert.id,
                    "rule_id": alert.rule_id,
                    "status": alert.vlm.status,
                },
            )
        )

    async def get(self, alert_id: str) -> Alert | None:
        return self._alerts.get(alert_id)

    async def update_vlm(self, alert_id: str, analysis: SceneAnalysis) -> None:
        alert = self._alerts.get(alert_id)
        if alert is None:
            return
        alert.update_vlm(analysis)
        await self.event_bus.publish(
            Event(
                type="alert.vlm_confirmed",
                source="alert_store",
                camera_id=alert.camera_id,
                payload={
                    "alert_id": alert_id,
                    "alert_level": analysis.alert_level.value,
                },
            )
        )

    async def list_active(self, camera_id: str) -> list[Alert]:
        return [
            a
            for a in self._alerts.values()
            if a.camera_id == camera_id and a.vlm.status == "pending"
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_alert_store.py -v
```
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/camera_ai/alert_store.py tests/test_alert_store.py
git commit -m "feat: add Alert model, AlertStore interface, InMemoryAlertStore"
```

---

### Task 4: VLMTask + VLMQueue + VLMWorker

**Files:**
- Create: `src/camera_ai/queue.py`
- Create: `tests/test_queue.py`

**Interfaces:**
- Consumes: `AlertStore` (Task 3), `EventBus` (Task 1), `SceneAnalysis` (schemas)
- Produces: `VLMTask`, `VLMQueue`, `VLMWorker` — dùng cho Task 6 (pipeline), Task 7 (API)

- [ ] **Step 1: Write the test file**

```python
# tests/test_queue.py
"""Tests for VLMQueue and VLMWorker."""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from camera_ai.queue import VLMTask, VLMQueue, VLMWorker
from camera_ai.schemas import AlertLevel, Detection, SceneAnalysis


def _make_task(
    task_id: str = "t1",
    priority: int = 3,
    camera_id: str = "cam_01",
) -> VLMTask:
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    return VLMTask(
        task_id=task_id,
        camera_id=camera_id,
        alert_id=f"alert-{task_id}",
        frames=[frame],
        detections=[Detection(label="person", confidence=0.9, bbox=[1, 2, 3, 4])],
        rule_id="test_rule",
        priority=priority,
        enqueued_at=time.monotonic(),
        max_keyframes=2,
    )


class TestVLMTask:
    def test_task_creation(self):
        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        task = VLMTask(
            task_id="t1",
            camera_id="cam_01",
            alert_id="alert-1",
            frames=[frame],
            detections=[],
            rule_id="intrusion",
            priority=2,
            enqueued_at=100.0,
            max_keyframes=4,
        )
        assert task.task_id == "t1"
        assert task.priority == 2
        assert len(task.frames) == 1
        assert task.max_keyframes == 4

    def test_task_ordering(self):
        """Lower priority number = higher urgency for PriorityQueue."""
        t1 = _make_task("high", priority=1, enqueued_at=100.0)
        t2 = _make_task("low", priority=4, enqueued_at=100.0)
        assert t1 < t2  # priority 1 < 4
        assert t2 > t1

    def test_tiebreaker_by_time(self):
        """Same priority → earlier enqueued wins."""
        t1 = _make_task("first", priority=2, enqueued_at=100.0)
        t2 = _make_task("second", priority=2, enqueued_at=200.0)
        assert t1 < t2


class TestVLMQueue:
    @pytest.fixture
    def queue(self):
        return VLMQueue()

    @pytest.mark.asyncio
    async def test_enqueue_dequeue_order(self, queue):
        t1 = _make_task("t1", priority=4)
        t2 = _make_task("t2", priority=1)  # higher urgency
        t3 = _make_task("t3", priority=2)

        await queue.enqueue(t1)
        await queue.enqueue(t2)
        await queue.enqueue(t3)

        assert await queue.dequeue() == t2  # priority 1 first
        assert await queue.dequeue() == t3  # priority 2 second
        assert await queue.dequeue() == t1  # priority 4 last

    @pytest.mark.asyncio
    async def test_depth(self, queue):
        assert queue.depth == 0
        await queue.enqueue(_make_task("t1"))
        assert queue.depth == 1
        await queue.enqueue(_make_task("t2"))
        assert queue.depth == 2
        await queue.dequeue()
        assert queue.depth == 1

    @pytest.mark.asyncio
    async def test_dynamic_priority_aging(self, queue):
        """Task waiting > threshold gets effective priority boost."""
        old_time = 50.0  # simulate enqueued 80s ago
        task = _make_task("old", priority=3, enqueued_at=old_time)

        # Patch time.monotonic to return "now"
        now = old_time + 80.0  # 80 seconds later
        original_monotonic = time.monotonic
        time.monotonic = lambda: now
        try:
            await queue.enqueue(task)
            dequeued = await queue.dequeue()
            # priority 3 + 80s wait → effective priority = 1 (floor)
            # but internal priority stays 3, effective used for ordering
            assert dequeued.task_id == "old"
        finally:
            time.monotonic = original_monotonic

    @pytest.mark.asyncio
    async def test_dequeue_empty_waits(self, queue):
        """Dequeue on empty queue should wait until item available."""
        async def delayed_enqueue():
            await asyncio.sleep(0.05)
            await queue.enqueue(_make_task("delayed"))

        start = time.monotonic()
        enqueue_task = asyncio.create_task(delayed_enqueue())
        result = await queue.dequeue()
        elapsed = time.monotonic() - start

        await enqueue_task
        assert result.task_id == "delayed"
        assert elapsed >= 0.04  # waited for enqueue


class TestVLMWorker:
    @pytest.mark.asyncio
    async def test_worker_processes_task(self):
        """Worker dequeues task, calls pipeline.analyze_vlm, updates alert."""
        queue = VLMQueue()
        mock_alert_store = AsyncMock()
        mock_pipeline = MagicMock()
        mock_event_bus = AsyncMock()

        analysis = SceneAnalysis(
            summary="Phân tích xong",
            alert_level=AlertLevel.MEDIUM,
        )
        mock_pipeline.analyze_vlm = MagicMock(return_value=analysis)

        worker = VLMWorker(
            queue=queue,
            pipeline=mock_pipeline,
            alert_store=mock_alert_store,
            event_bus=mock_event_bus,
        )

        # Start worker in background
        worker_task = asyncio.create_task(worker.run())

        # Enqueue a task
        task = _make_task("worker-test")
        await queue.enqueue(task)

        # Wait for worker to process
        await asyncio.sleep(0.1)

        # Stop worker
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

        # Verify
        mock_pipeline.analyze_vlm.assert_called_once_with(task)
        mock_alert_store.update_vlm.assert_called_once_with("alert-worker-test", analysis)

    @pytest.mark.asyncio
    async def test_worker_handles_pipeline_error(self):
        """Worker should not crash on pipeline error, just log and continue."""
        queue = VLMQueue()
        mock_alert_store = AsyncMock()
        mock_pipeline = MagicMock()
        mock_event_bus = AsyncMock()

        mock_pipeline.analyze_vlm = MagicMock(side_effect=RuntimeError("VLM crash"))

        worker = VLMWorker(
            queue=queue,
            pipeline=mock_pipeline,
            alert_store=mock_alert_store,
            event_bus=mock_event_bus,
        )

        worker_task = asyncio.create_task(worker.run())
        await queue.enqueue(_make_task("error-task"))
        await asyncio.sleep(0.1)

        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass

        # Alert store should NOT have been updated (pipeline failed)
        mock_alert_store.update_vlm.assert_not_called()
        # Worker should still be alive (not crashed)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_queue.py -v
```
Expected: FAIL — module not found

- [ ] **Step 3: Write implementation**

```python
# src/camera_ai/queue.py
"""VLM priority queue and background worker for async processing."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from .schemas import Detection, SceneAnalysis

if TYPE_CHECKING:
    from .alert_store import AlertStore
    from .events import EventBus
    from .pipeline import SecurityAIPipeline

logger = logging.getLogger(__name__)

# Priority aging thresholds
AGING_30S = 30.0
AGING_60S = 60.0
AGING_120S = 120.0


@dataclass(order=True)
class VLMTask:
    """One VLM analysis task in the priority queue."""

    # Sort fields (order=True uses these in declaration order)
    priority: int
    enqueued_at: float

    # Non-sort fields
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex, compare=False)
    camera_id: str = field(default="unknown", compare=False)
    alert_id: str = field(default="", compare=False)
    frames: list[np.ndarray] = field(default_factory=list, compare=False)
    detections: list[Detection] = field(default_factory=list, compare=False)
    rule_id: str = field(default="default", compare=False)
    max_keyframes: int = field(default=2, compare=False)

    def effective_priority(self, now: float | None = None) -> int:
        """Priority with aging: tasks waiting too long get boosted."""
        if now is None:
            now = time.monotonic()
        wait = now - self.enqueued_at
        boost = 0
        if wait > AGING_120S:
            boost = 3
        elif wait > AGING_60S:
            boost = 2
        elif wait > AGING_30S:
            boost = 1
        return max(1, self.priority - boost)


class VLMQueue:
    """Priority queue for VLM analysis tasks.

    Uses asyncio.PriorityQueue under the hood. Lower priority number = higher
    urgency. Dynamic priority aging prevents starvation of low-priority tasks.
    """

    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue[VLMTask] = asyncio.PriorityQueue()

    async def enqueue(self, task: VLMTask) -> None:
        # Re-wrap to capture effective priority at enqueue time
        eff = task.effective_priority()
        wrapper = VLMTask(
            priority=eff,
            enqueued_at=task.enqueued_at,
            task_id=task.task_id,
            camera_id=task.camera_id,
            alert_id=task.alert_id,
            frames=task.frames,
            detections=task.detections,
            rule_id=task.rule_id,
            max_keyframes=task.max_keyframes,
        )
        await self._queue.put(wrapper)
        logger.debug(
            "[vlm-queue] enqueued alert=%s priority=%s effective=%s depth=%s",
            task.alert_id,
            task.priority,
            eff,
            self._queue.qsize(),
        )

    async def dequeue(self) -> VLMTask:
        task = await self._queue.get()
        logger.debug(
            "[vlm-queue] dequeued alert=%s priority=%s depth=%s",
            task.alert_id,
            task.priority,
            self._queue.qsize(),
        )
        return task

    @property
    def depth(self) -> int:
        return self._queue.qsize()


class VLMWorker:
    """Background worker that dequeues VLM tasks and runs analysis.

    One worker = one asyncio task = one GPU inference slot. For multi-GPU,
    create multiple workers pointing to the same queue.
    """

    def __init__(
        self,
        queue: VLMQueue,
        pipeline: SecurityAIPipeline,
        alert_store: AlertStore,
        event_bus: EventBus,
    ) -> None:
        self._queue = queue
        self._pipeline = pipeline
        self._alert_store = alert_store
        self._event_bus = event_bus
        self._running = False

    async def run(self) -> None:
        """Infinite loop: dequeue → analyze → update. Run as asyncio task."""
        self._running = True
        logger.info("[vlm-worker] started")
        while self._running:
            try:
                task = await self._queue.dequeue()
                logger.info(
                    "[vlm-worker] processing alert=%s camera=%s rule=%s",
                    task.alert_id,
                    task.camera_id,
                    task.rule_id,
                )
                # analyze_vlm is sync (blocking Ollama I/O) — offload to thread
                analysis = await asyncio.to_thread(
                    self._pipeline.analyze_vlm, task
                )
                await self._alert_store.update_vlm(task.alert_id, analysis)
                logger.info(
                    "[vlm-worker] completed alert=%s level=%s",
                    task.alert_id,
                    analysis.alert_level.value,
                )
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(
                    "[vlm-worker] error processing alert=%s", task.alert_id
                )
        logger.info("[vlm-worker] stopped")

    async def stop(self) -> None:
        self._running = False
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_queue.py -v
```
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/camera_ai/queue.py tests/test_queue.py
git commit -m "feat: add VLMTask, VLMQueue with priority aging, VLMWorker"
```

---

### Task 5: Pipeline — Tách detect() và analyze_vlm()

**Files:**
- Modify: `src/camera_ai/pipeline.py`
- Modify: `tests/test_pipeline.py` — thêm test async methods

**Interfaces:**
- Consumes: `VLMTask` (Task 4), `VLMResult.status` (Task 2)
- Produces: `SecurityAIPipeline.detect()`, `SecurityAIPipeline.analyze_vlm()` — dùng cho Task 6 (VLMWorker), Task 7 (API)

- [ ] **Step 1: Add test for detect() returns pending status**

Thêm vào `tests/test_pipeline.py`:

```python
def test_detect_image_returns_pending_status():
    """detect() should return vlm.status='pending', not call VLM."""
    from camera_ai.pipeline import SecurityAIPipeline

    pipeline = SecurityAIPipeline(
        detector=DummyDetector(),
        vlm=MockAnalyzer(),
    )
    result = pipeline.detect(
        EventObject(image=_png_bytes(), camera_id="async_cam", media_type=MediaType.IMAGE)
    )
    assert result.vlm.status == "pending"
    assert result.vlm.skipped is False
    assert result.detections  # detection vẫn có


def test_detect_skips_vlm_when_gate_closed():
    """detect() with no detections should still return vlm.status='skipped'."""
    from camera_ai.pipeline import SecurityAIPipeline

    pipeline = SecurityAIPipeline(
        detector=EmptyDetector(),
        vlm=MockAnalyzer(),
    )
    result = pipeline.detect(
        EventObject(image=_png_bytes(), media_type=MediaType.IMAGE)
    )
    assert result.vlm.status == "skipped"
    assert result.vlm.skipped is True


def test_analyze_vlm_runs_on_task():
    """analyze_vlm() takes a VLMTask and returns SceneAnalysis."""
    from camera_ai.pipeline import SecurityAIPipeline
    from camera_ai.queue import VLMTask
    import time

    pipeline = SecurityAIPipeline(
        detector=DummyDetector(),
        vlm=MockAnalyzer(),
    )
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    task = VLMTask(
        task_id="test-task",
        camera_id="cam_01",
        alert_id="alert-1",
        frames=[frame],
        detections=[Detection(label="person", confidence=0.91, bbox=[1, 2, 3, 4])],
        rule_id="test_rule",
        priority=3,
        enqueued_at=time.monotonic(),
        max_keyframes=2,
    )
    analysis = pipeline.analyze_vlm(task)
    assert analysis.alert_level.value in ("low", "medium", "high")
    assert analysis.degraded is True  # mock


def test_analyze_event_still_works_backward_compat():
    """Existing analyze_event() should still work (backward compat)."""
    result = _make_pipeline(detector=DummyDetector()).analyze_event(
        EventObject(image=_png_bytes(), camera_id="cam_legacy", media_type=MediaType.IMAGE)
    )
    assert result.vlm.status == "completed"  # đồng bộ → completed ngay
    assert result.vlm.degraded is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_pipeline.py::test_detect_image_returns_pending_status -v
```
Expected: FAIL — `SecurityAIPipeline` has no `detect` method

- [ ] **Step 3: Implement detect(), analyze_vlm(), and _read_video_frames() helper**

Extract shared video-reading logic để `_analyze_video` và `_detect_video` cùng dùng:

```python
# src/camera_ai/pipeline.py — thêm helper và async methods

    # -- shared video helper ---------------------------------------------

    def _read_video_frames(
        self,
        source: str,
    ) -> tuple[list[np.ndarray], list[Detection], float, np.ndarray | None]:
        """Đọc video, chạy motion+YOLO. Trả về (sampled_frames, all_detections, source_fps, last_frame)."""
        cap = cv2.VideoCapture(source)
        if not cap.isOpened():
            raise ValueError("cannot open video input")
        source_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        motion_interval = max(1, round(source_fps / self.motion_fps))
        detector_interval = max(1, round(source_fps / self.yolo_fps))
        self.motion_detector.reset()
        sampled_frames: list[np.ndarray] = []
        all_detections: list[Detection] = []
        last_frame: np.ndarray | None = None
        last_detector_index: int | None = None
        idx = 0
        while True:
            if (
                self.max_video_windows is not None
                and idx >= source_fps * self.window_seconds * self.max_video_windows
            ):
                break
            grabbed = cap.grab()
            if not grabbed:
                break
            if idx % motion_interval == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                last_frame = frame
                frame = self._resize(frame, VIDEO_MAX_SIDE, interpolation=cv2.INTER_LINEAR)
                sampled_frames.append(frame)
                motion = self.motion_detector.compare(frame)
                if motion.motion and (
                    last_detector_index is None
                    or idx - last_detector_index >= detector_interval
                ):
                    try:
                        all_detections.extend(self.detector.detect(frame))
                        last_detector_index = idx
                    except Exception:
                        logger.exception("video detector failed at frame %s", idx)
            idx += 1
        cap.release()
        return sampled_frames, all_detections, source_fps, last_frame

    # -- async pipeline methods -----------------------------------------

    def detect(self, event: EventObject) -> PipelineResult:
        """Run detection + gate only. VLM runs later via queue. Returns immediately."""
        if event.media_type == MediaType.IMAGE:
            return self._detect_image(event)
        return self._detect_video(event)

    def _detect_image(self, event: EventObject) -> PipelineResult:
        frame = self._load_image(event.image)
        frame = self._resize(frame, IMAGE_MAX_SIDE)
        detections = self.detector.detect(frame)
        if not self.gate.decide(detections):
            return self._build_skipped(event, MediaType.IMAGE, detections, frame)
        return PipelineResult(
            media_type=MediaType.IMAGE,
            camera_id=event.camera_id,
            detections=detections,
            vlm=VLMResult(summary="", status="pending"),
            security=SecurityDecision(alert_level=AlertLevel.LOW),
            annotated_image=self._annotate(frame, detections),
        )

    def _detect_video(self, event: EventObject) -> PipelineResult:
        """Video detection only — VLM runs later via queue."""
        source = event.image
        tmp_path: str | None = None
        if isinstance(source, bytes):
            tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            tmp.write(source)
            tmp.close()
            tmp_path = tmp.name
            source = tmp_path
        try:
            sampled, all_detections, _source_fps, last_frame = (
                self._read_video_frames(source)
            )
        finally:
            if tmp_path:
                os.unlink(tmp_path)

        if last_frame is None:
            raise ValueError("no readable frames in video")

        if not self.gate.decide(all_detections):
            return self._build_skipped(event, MediaType.VIDEO, all_detections, last_frame)

        return PipelineResult(
            media_type=MediaType.VIDEO,
            camera_id=event.camera_id,
            detections=all_detections,
            vlm=VLMResult(summary="", status="pending"),
            security=SecurityDecision(alert_level=AlertLevel.LOW),
            annotated_image=self._annotate(last_frame, all_detections),
        )

    def analyze_vlm(self, task: VLMTask) -> SceneAnalysis:
        """Run VLM analysis on pre-detected frames. Called by VLMWorker (via asyncio.to_thread)."""
        if len(task.frames) == 1:
            return self.vlm.analyze(task.frames[0], task.detections)
        return self.vlm.analyze_sequence(task.frames, task.detections)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_pipeline.py -v
```
Expected: 12 passed (8 cũ + 4 mới)

- [ ] **Step 5: Commit**

```bash
git add src/camera_ai/pipeline.py tests/test_pipeline.py
git commit -m "feat: add detect() and analyze_vlm() for async pipeline"
```

---

### Task 6: API — Async Endpoints + VLMWorker Startup

**Files:**
- Modify: `apps/api/main.py`

**Interfaces:**
- Consumes: `VLMQueue`, `VLMWorker` (Task 4), `AlertStore` (Task 3), `EventBus` (Task 1), `SecurityAIPipeline.detect()` (Task 5)

- [ ] **Step 1: Update main.py — add async endpoints and worker lifecycle**

```python
# apps/api/main.py — thêm sau dòng `pipeline = _build_pipeline()`

import asyncio
import logging
import os
import time
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv

from camera_ai import SecurityAIPipeline
from camera_ai.alert_store import Alert, InMemoryAlertStore
from camera_ai.events import InProcessEventBus
from camera_ai.queue import VLMTask, VLMQueue, VLMWorker
from camera_ai.schemas import EventObject, MediaType, PipelineResult
from camera_ai.vlm.mock import MockAnalyzer

# ... (giữ nguyên code hiện tại: logging, load_dotenv, UI_FILE, _build_pipeline, pipeline, app)

# --- async pipeline infrastructure ---

event_bus = InProcessEventBus()
alert_store = InMemoryAlertStore(event_bus=event_bus)
vlm_queue = VLMQueue()
vlm_worker = VLMWorker(
    queue=vlm_queue,
    pipeline=pipeline,
    alert_store=alert_store,
    event_bus=event_bus,
)

@app.on_event("startup")
async def startup_vlm_worker() -> None:
    asyncio.create_task(vlm_worker.run())


@app.on_event("shutdown")
async def shutdown_vlm_worker() -> None:
    await vlm_worker.stop()


# --- async endpoints ---

VLM_MAX_SIDE = 640  # resize frame trước khi lưu vào VLMTask


@app.post("/async/analyze/image", response_model=PipelineResult)
async def analyze_image_async(
    file: UploadFile = File(...),
    camera_id: str = Form("unknown"),
) -> PipelineResult:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty upload")
    event = EventObject(camera_id=camera_id, image=content, media_type=MediaType.IMAGE)
    try:
        result = await asyncio.to_thread(pipeline.detect, event)
    except Exception as exc:
        logger.exception("async image detection failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if result.vlm.status == "pending":
        # Decode frame từ request bytes → lưu vào VLMTask.frames
        data = np.frombuffer(content, dtype=np.uint8)
        frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if frame is not None:
            h, w = frame.shape[:2]
            if max(h, w) > VLM_MAX_SIDE:
                scale = VLM_MAX_SIDE / max(h, w)
                frame = cv2.resize(
                    frame,
                    (int(w * scale), int(h * scale)),
                    interpolation=cv2.INTER_AREA,
                )

        task = VLMTask(
            camera_id=camera_id,
            alert_id=result.request_id,
            frames=[frame] if frame is not None else [],
            detections=result.detections,
            rule_id="default",
            priority=3,  # Phase 2: Rule Engine sets this
            enqueued_at=time.monotonic(),
            max_keyframes=1,
        )
        alert = Alert(
            id=result.request_id,
            camera_id=camera_id,
            vlm=result.vlm,
            security=result.security,
        )
        await alert_store.create(alert)
        await vlm_queue.enqueue(task)

    return result


@app.get("/alerts/{alert_id}")
async def get_alert(alert_id: str) -> dict:
    alert = await alert_store.get(alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    return alert.to_dict()


@app.get("/alerts")
async def list_alerts(camera_id: str | None = None) -> list[dict]:
    if camera_id:
        return [a.to_dict() for a in await alert_store.list_active(camera_id)]
    return [a.to_dict() for a in alert_store._alerts.values()]


@app.get("/health")
def health() -> dict[str, str | int]:
    return {
        "status": "ok",
        "vlm_queue_depth": vlm_queue.depth,
    }
```

- [ ] **Step 2: Test end-to-end async flow**

```bash
# Start server
.venv/Scripts/python.exe -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8001 &

# Test async image
curl -s -w "\nHTTP:%{http_code} TIME:%{time_total}s\n" \
  -F "file=@.venv/Lib/site-packages/ultralytics/assets/bus.jpg" \
  -F "camera_id=test_async" \
  http://127.0.0.1:8001/async/analyze/image -o async_resp.json

# Response phải có vlm.status="pending" và thời gian < 2s
.venv/Scripts/python.exe -c "
import json
d = json.load(open('async_resp.json'))
print('status:', d['vlm']['status'])
print('detections:', len(d['detections']))
assert d['vlm']['status'] == 'pending', 'Expected pending status'
print('OK - async response confirmed')
"

# Poll alert status
sleep 6
alert_id=$(.venv/Scripts/python.exe -c "import json; print(json.load(open('async_resp.json'))['request_id'])")
curl -s http://127.0.0.1:8001/alerts/$alert_id | .venv/Scripts/python.exe -c "
import json, sys
d = json.load(sys.stdin)
print('alert status:', d['vlm']['status'])
print('summary:', d['vlm']['summary'][:80])
"

# Cleanup
rm -f async_resp.json
```

- [ ] **Step 3: Run existing tests to verify no regression**

```bash
python -m pytest tests/ -v
```
Expected: all tests pass

- [ ] **Step 4: Commit**

```bash
git add apps/api/main.py
git commit -m "feat: add /async/analyze/image, GET /alerts/{id}, VLMWorker startup"
```

---

### Task 7: End-to-End Test + Cleanup

**Files:**
- Create: `tests/test_async_pipeline.py`

- [ ] **Step 1: Write end-to-end test**

```python
# tests/test_async_pipeline.py
"""End-to-end test: detect → queue → worker → alert completion."""
import asyncio
import time

import numpy as np
import pytest

from camera_ai import SecurityAIPipeline
from camera_ai.alert_store import Alert, InMemoryAlertStore
from camera_ai.events import InProcessEventBus
from camera_ai.queue import VLMTask, VLMQueue, VLMWorker
from camera_ai.schemas import EventObject, MediaType
from camera_ai.vlm.mock import MockAnalyzer


class DummyDetector:
    def detect(self, frame: np.ndarray):
        from camera_ai.schemas import Detection
        return [Detection(label="person", confidence=0.91, bbox=[1, 2, 3, 4])]


@pytest.mark.asyncio
async def test_detect_then_vlm_async_flow():
    """Image detection → VLM queue → worker → alert completed."""
    pipeline = SecurityAIPipeline(
        detector=DummyDetector(),
        vlm=MockAnalyzer(),
    )
    queue = VLMQueue()
    event_bus = InProcessEventBus()
    alert_store = InMemoryAlertStore(event_bus=event_bus)

    worker = VLMWorker(
        queue=queue,
        pipeline=pipeline,
        alert_store=alert_store,
        event_bus=event_bus,
    )
    worker_task = asyncio.create_task(worker.run())

    # 1. Detect (fast)
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    result = pipeline.detect(
        EventObject(image=bytes(frame), camera_id="e2e", media_type=MediaType.IMAGE)
    )
    assert result.vlm.status == "pending"

    # 2. Create alert + enqueue
    alert = Alert(
        id=result.request_id,
        camera_id="e2e",
        vlm=result.vlm,
        security=result.security,
    )
    await alert_store.create(alert)

    task = VLMTask(
        task_id="e2e-task",
        camera_id="e2e",
        alert_id=result.request_id,
        frames=[frame],
        detections=result.detections,
        rule_id="e2e_rule",
        priority=3,
        enqueued_at=time.monotonic(),
        max_keyframes=2,
    )
    await queue.enqueue(task)

    # 3. Wait for worker to process
    for _ in range(20):  # max 2s
        await asyncio.sleep(0.1)
        updated = await alert_store.get(result.request_id)
        if updated is not None and updated.vlm.status == "completed":
            break

    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass

    # 4. Verify
    final = await alert_store.get(result.request_id)
    assert final is not None
    assert final.vlm.status == "completed"
    assert final.vlm.summary  # Mock trả về summary
    assert not final.vlm.skipped
```

- [ ] **Step 2: Run end-to-end test**

```bash
python -m pytest tests/test_async_pipeline.py -v
```
Expected: PASS

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/ -v
```
Expected: all tests pass (~32: 8 cũ + 5 events + 7 alert + 7 queue + 4 pipeline async + 1 e2e)

- [ ] **Step 4: Commit**

```bash
git add tests/test_async_pipeline.py
git commit -m "test: add end-to-end async pipeline test"
```
