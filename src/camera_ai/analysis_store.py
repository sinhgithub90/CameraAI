"""Aggregate lifecycle state for one asynchronously processed video."""
from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from .alert_cooldown import VerificationStatus
from .schemas import SceneAnalysis, StageTiming, VideoWindowResult, VLMResult, SecurityDecision
from .video_windows import ProcessedVideoWindow


class VideoAnalysis(BaseModel):
    id: str
    camera_id: str
    status: str = "reading"
    windows: list[VideoWindowResult] = Field(default_factory=list)
    total_timing: StageTiming = Field(default_factory=StageTiming)
    error: str | None = None
    producer_finished: bool = False

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
        analysis.refresh_total_timing()
        self._refresh_status(analysis)

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
