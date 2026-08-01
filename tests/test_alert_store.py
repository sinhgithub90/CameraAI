# tests/test_alert_store.py
"""Tests for AlertStore interface and InMemoryAlertStore."""
import asyncio

import pytest

from camera_ai.alert_store import Alert, AlertStore, InMemoryAlertStore
from camera_ai.events import Event, InProcessEventBus
from camera_ai.schemas import AlertLevel, SceneAnalysis, SecurityDecision, StageTiming, VLMResult


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
    async def test_update_vlm_records_window_stage_timing(self, store):
        alert = Alert(
            id="timed",
            camera_id="cam_01",
            timing=StageTiming(motion_ms=12.0, detector_ms=34.0),
        )
        await store.create(alert)

        await store.update_vlm(
            "timed",
            SceneAnalysis(summary="done", alert_level=AlertLevel.LOW),
            qwen_ms=56.0,
        )

        saved = await store.get("timed")
        assert saved is not None
        assert saved.timing.motion_ms == 12.0
        assert saved.timing.detector_ms == 34.0
        assert saved.timing.qwen_ms == 56.0
        assert saved.timing.total_ms == 102.0

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
