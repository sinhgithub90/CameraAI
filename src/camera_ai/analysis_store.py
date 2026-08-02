"""Aggregate lifecycle state for one asynchronously processed video."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, Field

from .alert_cooldown import (
    AlertRuntimePhase,
    RED_COOLDOWN_SECONDS,
    VerificationStatus,
    WindowAdmission,
    WindowAlertContext,
)
from .schemas import (
    AlertLevel,
    SceneAnalysis,
    SecurityDecision,
    StageTiming,
    VideoWindowResult,
    VLMResult,
)
from .video_windows import ProcessedVideoWindow


class CooldownSummary(BaseModel):
    active_alert_id: str | None = None
    alert_level: AlertLevel = AlertLevel.LOW
    timebase: Literal["video", "monotonic"] = "video"
    red_started: float | None = None
    recheck_at: float | None = None
    suppressed_windows: int = 0
    suppressed_seconds: float = 0.0


class VideoAnalysis(BaseModel):
    id: str
    camera_id: str
    status: str = "reading"
    windows: list[VideoWindowResult] = Field(default_factory=list)
    total_timing: StageTiming = Field(default_factory=StageTiming)
    error: str | None = None
    producer_finished: bool = False
    cooldown: CooldownSummary | None = None

    def refresh_total_timing(self) -> None:
        self.total_timing = StageTiming(
            motion_ms=sum(window.timing.motion_ms for window in self.windows),
            detector_ms=sum(window.timing.detector_ms for window in self.windows),
            keyframe_ms=sum(window.timing.keyframe_ms for window in self.windows),
            qwen_ms=sum(window.timing.qwen_ms for window in self.windows),
            queue_wait_ms=sum(window.timing.queue_wait_ms for window in self.windows),
            wall_clock_ms=sum(window.timing.wall_clock_ms for window in self.windows),
        )
        self.total_timing.total_ms = (
            self.total_timing.motion_ms
            + self.total_timing.detector_ms
            + self.total_timing.keyframe_ms
            + self.total_timing.qwen_ms
        )


class CompactWindowQwen(BaseModel):
    status: str
    summary: str
    degraded: bool = False
    verified: bool = False
    reason: str | None = None


class CompactWindowCooldown(BaseModel):
    active_alert_id: str | None = None
    next_recheck_seconds: float | None = None
    recheck: bool | None = None
    episode_created: bool | None = None
    episode_extended: bool | None = None
    episode_resolved: bool | None = None


class CompactVideoWindow(BaseModel):
    window_index: int
    start_seconds: float
    end_seconds: float
    alert_level: AlertLevel
    qwen: CompactWindowQwen
    cooldown: CompactWindowCooldown | None = None
    timing: StageTiming


class CompactVideoAnalysis(BaseModel):
    id: str
    camera_id: str
    status: str
    error: str | None = None
    total_timing: StageTiming
    windows: list[CompactVideoWindow]
    cooldown: CooldownSummary | None = None

    @classmethod
    def from_analysis(cls, analysis: VideoAnalysis) -> CompactVideoAnalysis:
        windows: list[CompactVideoWindow] = []
        for window in analysis.windows:
            metadata = window.event_metadata or {}
            vlm_call = metadata.get("vlm_call") or {}
            alert_context = metadata.get("alert_context") or {}
            verification_status = alert_context.get("verification_status")
            cooldown_values = {
                "active_alert_id": alert_context.get("active_alert_id"),
                "next_recheck_seconds": alert_context.get(
                    "next_recheck_event_seconds"
                ),
                "recheck": True if alert_context.get("recheck") else None,
                "episode_created": (
                    True if alert_context.get("episode_created") else None
                ),
                "episode_extended": (
                    True if alert_context.get("episode_extended") else None
                ),
                "episode_resolved": (
                    True if alert_context.get("episode_resolved") else None
                ),
            }
            cooldown = (
                CompactWindowCooldown(**cooldown_values)
                if any(value is not None for value in cooldown_values.values())
                else None
            )
            windows.append(
                CompactVideoWindow(
                    window_index=window.window_index,
                    start_seconds=window.start_seconds,
                    end_seconds=window.end_seconds,
                    alert_level=window.security.alert_level,
                    qwen=CompactWindowQwen(
                        status=window.vlm.status,
                        summary=window.vlm.summary,
                        degraded=window.vlm.degraded,
                        verified=verification_status == "verified",
                        reason=vlm_call.get("reason"),
                    ),
                    cooldown=cooldown,
                    timing=window.timing,
                )
            )
        return cls(
            id=analysis.id,
            camera_id=analysis.camera_id,
            status=analysis.status,
            error=analysis.error,
            total_timing=analysis.total_timing,
            windows=windows,
            cooldown=analysis.cooldown,
        )


class AnalysisStore(ABC):
    @abstractmethod
    async def create(self, analysis: VideoAnalysis) -> None: ...

    @abstractmethod
    async def get(self, analysis_id: str) -> VideoAnalysis | None: ...

    @abstractmethod
    async def has_active_camera(self, camera_id: str) -> bool:
        """Return whether the camera already has a reading or queued analysis."""
        ...

    @abstractmethod
    async def append_window(self, analysis_id: str, window: VideoWindowResult) -> None: ...

    @abstractmethod
    async def complete_window(
        self, analysis_id: str, alert_id: str, scene: SceneAnalysis, qwen_ms: float
    ) -> None: ...

    @abstractmethod
    async def mark_producer_complete(self, analysis_id: str) -> None: ...

    @abstractmethod
    async def mark_producer_failed(self, analysis_id: str, detail: str) -> None: ...

    @abstractmethod
    async def complete_processed_window(self, analysis_id: str, alert_id: str, processed: ProcessedVideoWindow, qwen_ms: float) -> None: ...

    @abstractmethod
    async def record_suppressed_window(
        self,
        analysis_id: str,
        admission: WindowAdmission,
        *,
        timebase: Literal["video", "monotonic"],
    ) -> None: ...

    @abstractmethod
    async def remove_pending_windows(
        self,
        analysis_id: str,
        alert_ids: set[str],
        *,
        context: WindowAlertContext,
        timebase: Literal["video", "monotonic"],
    ) -> list[str]: ...

    @abstractmethod
    async def discard_pending_window(
        self, analysis_id: str, alert_id: str
    ) -> bool: ...


class InMemoryAnalysisStore(AnalysisStore):
    def __init__(self) -> None:
        self._analyses: dict[str, VideoAnalysis] = {}

    async def create(self, analysis: VideoAnalysis) -> None:
        self._analyses[analysis.id] = analysis

    async def get(self, analysis_id: str) -> VideoAnalysis | None:
        return self._analyses.get(analysis_id)

    async def has_active_camera(self, camera_id: str) -> bool:
        return any(
            analysis.camera_id == camera_id
            and analysis.status in {"reading", "queued"}
            for analysis in self._analyses.values()
        )

    async def append_window(self, analysis_id: str, window: VideoWindowResult) -> None:
        analysis = self._analyses[analysis_id]
        analysis.windows.append(window)
        analysis.status = "queued"
        analysis.refresh_total_timing()

    async def complete_window(
        self, analysis_id: str, alert_id: str, scene: SceneAnalysis, qwen_ms: float
    ) -> None:
        analysis = self._analyses[analysis_id]
        for window in analysis.windows:
            if window.alert_id == alert_id:
                window.vlm = VLMResult(
                    summary=scene.summary,
                    observations=scene.observations,
                    degraded=scene.degraded,
                    status="completed",
                )
                window.security = SecurityDecision(
                    alert_level=scene.alert_level,
                    risks=scene.risks,
                    recommended_action=scene.recommended_action,
                )
                window.timing.qwen_ms = qwen_ms
                window.timing.total_ms = (
                    window.timing.motion_ms
                    + window.timing.detector_ms
                    + window.timing.keyframe_ms
                    + window.timing.qwen_ms
                )
                break
        analysis.refresh_total_timing()
        self._refresh_status(analysis)

    async def mark_producer_complete(self, analysis_id: str) -> None:
        analysis = self._analyses[analysis_id]
        analysis.producer_finished = True
        self._refresh_status(analysis)

    async def mark_producer_failed(self, analysis_id: str, detail: str) -> None:
        analysis = self._analyses[analysis_id]
        analysis.status = "failed"
        analysis.error = detail

    async def complete_processed_window(self, analysis_id: str, alert_id: str, processed: ProcessedVideoWindow, qwen_ms: float) -> None:
        analysis = self._analyses[analysis_id]
        vlm_skipped = not processed.vlm_call.call_vlm
        vlm_status = (
            "suppressed"
            if processed.alert_context.verification_status
            is VerificationStatus.SUPPRESSED
            else "skipped"
            if vlm_skipped
            else "completed"
        )
        for window in analysis.windows:
            if window.alert_id == alert_id:
                window.detections = processed.detections
                window.qwen_input = processed.qwen_input
                window.timing = processed.timing
                window.vlm = VLMResult(summary=processed.scene.summary, observations=processed.scene.observations, degraded=processed.scene.degraded, skipped=vlm_skipped, status=vlm_status)
                window.security = SecurityDecision(alert_level=processed.scene.alert_level, risks=processed.scene.risks, recommended_action=processed.scene.recommended_action)
                window.event_metadata = {
                    "observation": processed.observation.model_dump(mode="json"),
                    "candidates": [item.model_dump(mode="json") for item in processed.candidates],
                    "decision": processed.decision.model_dump(mode="json") if processed.decision else None,
                    "alert": processed.alert_event.model_dump(mode="json") if processed.alert_event else None,
                    "vlm_call": processed.vlm_call.model_dump(mode="json"),
                    "alert_context": processed.alert_context.model_dump(mode="json"),
                    "vlm_trace": {
                        "prompt": processed.vlm_trace.prompt,
                        "raw_output": processed.vlm_trace.raw_output,
                        "raw_output_valid": processed.vlm_trace.raw_output_valid,
                        "decision": processed.vlm_trace.decision,
                        "event_type": processed.vlm_trace.event_type,
                        "evidence": processed.vlm_trace.evidence,
                    } if processed.vlm_trace else None,
                }
                break
        context = processed.alert_context
        if context.episode_resolved and analysis.cooldown is not None:
            analysis.cooldown.active_alert_id = None
            analysis.cooldown.alert_level = AlertLevel.LOW
            analysis.cooldown.recheck_at = None
        elif (
            context.active_alert_id is not None
            and context.state is not AlertRuntimePhase.NORMAL
        ):
            self._add_cooldown_suppression(
                analysis,
                active_alert_id=context.active_alert_id,
                alert_level=context.effective_level,
                timebase="video",
                red_started=(
                    context.window_end_seconds if context.episode_created else None
                ),
                recheck_at=context.next_recheck_event_seconds,
                windows=0,
                seconds=0.0,
            )
        analysis.refresh_total_timing()
        self._refresh_status(analysis)

    async def record_suppressed_window(
        self,
        analysis_id: str,
        admission: WindowAdmission,
        *,
        timebase: Literal["video", "monotonic"],
    ) -> None:
        analysis = self._analyses[analysis_id]
        red_started = (
            admission.next_recheck_event_seconds - RED_COOLDOWN_SECONDS
            if admission.effective_level is AlertLevel.HIGH
            and admission.next_recheck_event_seconds is not None
            else None
        )
        self._add_cooldown_suppression(
            analysis,
            active_alert_id=admission.active_alert_id,
            alert_level=admission.effective_level,
            timebase=timebase,
            red_started=red_started,
            recheck_at=admission.next_recheck_event_seconds,
            windows=1,
            seconds=max(0.0, admission.end_seconds - admission.start_seconds),
        )

    async def remove_pending_windows(
        self,
        analysis_id: str,
        alert_ids: set[str],
        *,
        context: WindowAlertContext,
        timebase: Literal["video", "monotonic"],
    ) -> list[str]:
        analysis = self._analyses[analysis_id]
        removed = [
            window
            for window in analysis.windows
            if window.alert_id in alert_ids and window.vlm.status == "pending"
        ]
        removed_ids = [window.alert_id for window in removed]
        if not removed:
            return removed_ids
        removed_set = set(removed_ids)
        analysis.windows = [
            window for window in analysis.windows if window.alert_id not in removed_set
        ]
        self._add_cooldown_suppression(
            analysis,
            active_alert_id=context.active_alert_id,
            alert_level=context.effective_level,
            timebase=timebase,
            red_started=context.window_end_seconds,
            recheck_at=context.next_recheck_event_seconds,
            windows=len(removed),
            seconds=sum(
                max(0.0, window.end_seconds - window.start_seconds)
                for window in removed
            ),
        )
        analysis.refresh_total_timing()
        self._refresh_status(analysis)
        return removed_ids

    async def discard_pending_window(
        self, analysis_id: str, alert_id: str
    ) -> bool:
        analysis = self._analyses[analysis_id]
        before = len(analysis.windows)
        analysis.windows = [
            window
            for window in analysis.windows
            if not (window.alert_id == alert_id and window.vlm.status == "pending")
        ]
        removed = len(analysis.windows) != before
        if removed:
            analysis.refresh_total_timing()
            self._refresh_status(analysis)
        return removed

    @staticmethod
    def _add_cooldown_suppression(
        analysis: VideoAnalysis,
        *,
        active_alert_id: str | None,
        alert_level: AlertLevel,
        timebase: Literal["video", "monotonic"],
        red_started: float | None,
        recheck_at: float | None,
        windows: int,
        seconds: float,
    ) -> None:
        summary = analysis.cooldown or CooldownSummary(timebase=timebase)
        episode_changed = (
            active_alert_id is not None
            and active_alert_id != summary.active_alert_id
        )
        summary.active_alert_id = active_alert_id
        summary.alert_level = alert_level
        summary.timebase = timebase
        if episode_changed or summary.red_started is None:
            summary.red_started = red_started
        summary.recheck_at = recheck_at
        summary.suppressed_windows += windows
        summary.suppressed_seconds += seconds
        analysis.cooldown = summary

    @staticmethod
    def _refresh_status(analysis: VideoAnalysis) -> None:
        if analysis.status == "failed":
            return
        if analysis.producer_finished and all(
            window.vlm.status in {"completed", "skipped", "suppressed"}
            for window in analysis.windows
        ):
            analysis.status = "completed"
        elif analysis.windows:
            analysis.status = "queued"
        else:
            analysis.status = "reading"
