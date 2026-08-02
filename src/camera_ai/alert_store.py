# src/camera_ai/alert_store.py
"""Alert persistence — in-memory store for async pipeline results."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel

from .alert_cooldown import VerificationStatus, WindowAlertContext
from .events import Event, EventBus
from .schemas import AlertLevel, SceneAnalysis, SecurityDecision, StageTiming, VLMResult


class AlertEpisode(BaseModel):
    id: str
    camera_id: str
    event_type: str | None = None
    status: str = "active"
    current_level: AlertLevel = AlertLevel.HIGH
    started_event_seconds: float
    last_verified_event_seconds: float
    next_recheck_event_seconds: float | None = None
    extension_count: int = 0
    resolved_event_seconds: float | None = None


class Alert:
    """One alert in the async pipeline lifecycle."""

    def __init__(
        self,
        id: str,
        camera_id: str,
        rule_id: str = "default",
        vlm: VLMResult | None = None,
        security: SecurityDecision | None = None,
        timing: StageTiming | None = None,
        created_at: datetime | None = None,
    ) -> None:
        self.id = id
        self.camera_id = camera_id
        self.rule_id = rule_id
        self.vlm = vlm or VLMResult(summary="", status="pending")
        self.security = security or SecurityDecision(alert_level=AlertLevel.LOW)
        self.timing = timing or StageTiming()
        self.created_at = created_at or datetime.now()

    def update_vlm(self, analysis: SceneAnalysis, qwen_ms: float = 0.0) -> None:
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
        self.timing.qwen_ms = qwen_ms
        self.timing.total_ms = (
            self.timing.motion_ms + self.timing.detector_ms + self.timing.qwen_ms
        )

    def to_dict(self) -> dict:
        """Serialize for API response."""
        return {
            "id": self.id,
            "camera_id": self.camera_id,
            "rule_id": self.rule_id,
            "vlm": self.vlm.model_dump(),
            "security": self.security.model_dump(),
            "timing": self.timing.model_dump(),
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
    async def update_vlm(
        self, alert_id: str, analysis: SceneAnalysis, qwen_ms: float = 0.0
    ) -> None:
        """Apply VLM analysis result to an existing alert."""
        ...

    @abstractmethod
    async def list_active(self, camera_id: str) -> list[Alert]:
        """List alerts with vlm.status == 'pending' for a camera."""
        ...

    @abstractmethod
    async def apply_episode_context(self, context: WindowAlertContext) -> None:
        """Create or update one business alert episode idempotently."""
        ...

    @abstractmethod
    async def get_episode(self, alert_id: str) -> AlertEpisode | None:
        """Retrieve a business alert episode by ID."""
        ...


class InMemoryAlertStore(AlertStore):
    """Dict-backed store. Alerts lost on restart — acceptable for Phase 1."""

    def __init__(self, event_bus: EventBus) -> None:
        self._alerts: dict[str, Alert] = {}
        self._episodes: dict[str, AlertEpisode] = {}
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

    async def update_vlm(
        self, alert_id: str, analysis: SceneAnalysis, qwen_ms: float = 0.0
    ) -> None:
        alert = self._alerts.get(alert_id)
        if alert is None:
            return
        alert.update_vlm(analysis, qwen_ms=qwen_ms)
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

    async def apply_episode_context(self, context: WindowAlertContext) -> None:
        alert_id = context.active_alert_id
        if alert_id is None:
            return
        episode = self._episodes.get(alert_id)
        event_type = context.event_type
        event_name: str | None = None
        if context.episode_created and episode is None:
            episode = AlertEpisode(
                id=alert_id,
                camera_id=context.camera_id,
                event_type=event_type,
                status="active",
                current_level=context.effective_level,
                started_event_seconds=context.window_start_seconds,
                last_verified_event_seconds=context.window_end_seconds,
                next_recheck_event_seconds=context.next_recheck_event_seconds,
            )
            self._episodes[alert_id] = episode
            event_name = "alert.episode_created"
        elif episode is None:
            return

        if context.episode_extended:
            episode.extension_count += 1
            episode.status = "active"
            event_name = "alert.episode_extended"
        if context.episode_resolved:
            episode.status = "resolved"
            episode.resolved_event_seconds = context.window_end_seconds
            event_name = "alert.episode_resolved"
        if context.verification_status is VerificationStatus.VERIFIED:
            episode.current_level = context.effective_level
            episode.last_verified_event_seconds = context.window_end_seconds
            episode.next_recheck_event_seconds = context.next_recheck_event_seconds
        if event_type is not None:
            episode.event_type = event_type

        if event_name is not None:
            await self.event_bus.publish(
                Event(
                    type=event_name,
                    source="alert_store",
                    camera_id=context.camera_id,
                    payload={
                        "alert_id": alert_id,
                        "camera_id": context.camera_id,
                    },
                )
            )

    async def get_episode(self, alert_id: str) -> AlertEpisode | None:
        episode = self._episodes.get(alert_id)
        return episode.model_copy(deep=True) if episode is not None else None
