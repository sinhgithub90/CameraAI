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
