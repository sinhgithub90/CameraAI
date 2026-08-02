"""Self-contained contracts and processing for five-second video windows.

This module owns decoding/sampling a source into windows and running the
per-window Motion -> detector -> VLM workflow.  It deliberately has no
dependency on the application queue, HTTP layer, or analysis persistence.
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from collections.abc import Callable

import cv2
import numpy as np
from pydantic import BaseModel, Field

from .alert_cooldown import (
    CameraAlertStateStore,
    CooldownGateDecision,
    VerificationStatus,
    WindowAdmission,
    WindowAlertContext,
)
from .detectors.base import Detector
from .detectors.motion import MotionDetector
from .schemas import (
    AlertLevel,
    Detection,
    EventObject,
    QwenInputSummary,
    SceneAnalysis,
    StageTiming,
    VideoFrameObservation,
    VLMAnalysisTrace,
)
from .video_selection import select_keyframes
from .vlm import VLMAnalyzer
from .artifacts import WindowArtifactWriter
from .event_models import (
    AlertEvent,
    CandidateEvent,
    ModelDecision,
    alert_from_decision,
    decision_from_trace,
    select_primary_candidate,
    Priority,
    stable_event_id,
)
from .router import route_observation
from .schemas import VideoWindowObservation
from .temporal_validation import TemporalSignalValidator
from .vlm_policy import VLMCallDecision, VLMCallReason, decide_vlm_call
from .detection_aggregation import aggregate_window_detections

VIDEO_MAX_SIDE = 1280
logger = logging.getLogger(__name__)


class RawVideoWindow(BaseModel):
    window_index: int
    start_seconds: float
    window_seconds: float = 5.0
    observations: list[VideoFrameObservation] = Field(default_factory=list)

    @property
    def end_seconds(self) -> float:
        return self.start_seconds + self.window_seconds


class ProcessedVideoWindow(BaseModel):
    scene: SceneAnalysis
    detections: list[Detection] = Field(default_factory=list)
    qwen_input: QwenInputSummary
    timing: StageTiming
    observation: VideoWindowObservation
    candidates: list[CandidateEvent] = Field(default_factory=list)
    decision: ModelDecision | None = None
    alert_event: AlertEvent | None = None
    vlm_trace: VLMAnalysisTrace | None = None
    vlm_call: VLMCallDecision
    alert_context: WindowAlertContext = Field(default_factory=WindowAlertContext)


class StreamWindowProducer:
    """Decode a source once and emit independently processable time windows."""

    def __init__(
        self,
        *,
        motion_fps: float,
        window_seconds: float,
        max_side: int = VIDEO_MAX_SIDE,
    ) -> None:
        self.motion_fps = motion_fps
        self.window_seconds = window_seconds
        self.max_side = max_side

    def stream(self, event: EventObject, on_window: Callable[[RawVideoWindow], None]) -> None:
        """Emit the first segment immediately and pace later source-time segments."""
        source = event.image
        tmp_path: str | None = None
        if isinstance(source, bytes):
            tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            try:
                tmp.write(source)
            finally:
                tmp.close()
            tmp_path = tmp.name
            source = tmp_path
        cap: cv2.VideoCapture | None = None
        try:
            cap = cv2.VideoCapture(source)
            if not cap.isOpened():
                raise ValueError("cannot open video input")
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            interval = max(1, round(fps / self.motion_fps))
            started = time.monotonic()
            idx = 0
            current_idx = 0
            observations: list[VideoFrameObservation] = []

            def flush() -> None:
                if observations:
                    on_window(
                        RawVideoWindow(
                            window_index=current_idx,
                            start_seconds=current_idx * self.window_seconds,
                            window_seconds=self.window_seconds,
                            observations=list(observations),
                        )
                    )

            while cap.grab():
                if idx % interval == 0:
                    timestamp = idx / fps
                    ok, frame = cap.retrieve()
                    if not ok:
                        break
                    window_idx = int(timestamp // self.window_seconds)
                    if window_idx != current_idx:
                        flush()
                        delay = started + window_idx * self.window_seconds - time.monotonic()
                        if delay > 0:
                            time.sleep(delay)
                        observations.clear()
                        current_idx = window_idx
                    observations.append(
                        VideoFrameObservation(
                            frame_index=idx,
                            timestamp_seconds=timestamp,
                            frame=self._resize(frame),
                        )
                    )
                idx += 1
            flush()
        finally:
            if cap is not None:
                cap.release()
            if tmp_path:
                os.unlink(tmp_path)

    def _resize(self, frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        if max(height, width) <= self.max_side:
            return frame
        scale = self.max_side / max(height, width)
        return cv2.resize(
            frame,
            (int(width * scale), int(height * scale)),
            interpolation=cv2.INTER_LINEAR,
        )


class VideoWindowProcessor:
    """Run all inference for one closed raw window without queue/store state."""

    def __init__(
        self,
        *,
        detector: Detector,
        fire_detector: Detector | None = None,
        vlm: VLMAnalyzer,
        yolo_fps: float,
        max_keyframes: int,
        artifact_writer: WindowArtifactWriter | None = None,
        alert_state_store: CameraAlertStateStore | None = None,
    ) -> None:
        self.detector = detector
        self.fire_detector = fire_detector
        self.vlm = vlm
        self.yolo_fps = yolo_fps
        self.max_keyframes = max_keyframes
        self.artifact_writer = artifact_writer
        self.alert_state_store = alert_state_store

    def process(
        self,
        window: RawVideoWindow,
        camera_id: str = "unknown",
        stream_id: str = "default",
        admission: WindowAdmission | None = None,
    ) -> ProcessedVideoWindow:
        admission = admission or (
            self.alert_state_store.inspect_window(
                stream_id=stream_id,
                camera_id=camera_id,
                start_seconds=window.start_seconds,
                end_seconds=window.end_seconds,
                processing_now=time.monotonic(),
            )
            if self.alert_state_store is not None
            else WindowAdmission.normal(
                stream_id=stream_id,
                camera_id=camera_id,
                start_seconds=window.start_seconds,
                end_seconds=window.end_seconds,
            )
        )
        if not admission.process_window:
            return self._build_suppressed_window(window, camera_id, admission)

        motion_detector = MotionDetector()
        detections: list[Detection] = []
        motion_ms = detector_ms = 0.0
        last_detector_seconds = float("-inf")
        fire_validator = TemporalSignalValidator()
        fire_confirmed = False
        fire_evidence: list[Detection] = []
        for observation in window.observations:
            started = time.perf_counter()
            observation.motion = motion_detector.compare(observation.frame)
            motion_ms += (time.perf_counter() - started) * 1000
            if (
                observation.motion.motion
                and observation.timestamp_seconds - last_detector_seconds >= 1 / self.yolo_fps
            ):
                started = time.perf_counter()
                observation.detections = self.detector.detect(observation.frame)
                detector_ms += (time.perf_counter() - started) * 1000
                last_detector_seconds = observation.timestamp_seconds
            if self.fire_detector is not None:
                fire_detections = self.fire_detector.detect(observation.frame)
                observation.detections.extend(fire_detections)
                fire_signal = fire_validator.update(
                    observation.frame_index, fire_detections
                )
                if fire_signal.confirmed:
                    fire_confirmed = True
                    fire_evidence = fire_signal.detections
            detections.extend(observation.detections)

        started = time.perf_counter()
        keyframes = select_keyframes(window.observations, max_keyframes=self.max_keyframes)
        keyframe_ms = (time.perf_counter() - started) * 1000
        frames = [item.frame for item in keyframes if item.frame is not None]
        detection_aggregate = aggregate_window_detections(window.observations)
        routing_peak = max(window.observations, key=lambda item: item.motion.score)
        routing_observation = VideoWindowObservation(
            camera_id=camera_id,
            window_id=f"{camera_id}_{window.window_index:06d}",
            start_ms=round(window.start_seconds * 1000),
            end_ms=round(window.end_seconds * 1000),
            motion=routing_peak.motion,
            detections=detections,
            selected_frames=[item.frame_index for item in keyframes],
            detection_aggregate=detection_aggregate,
        )
        routing_candidates = route_observation(routing_observation)
        if fire_confirmed:
            fire_type = "temporally_confirmed_fire_signal"
            routing_candidates.append(
                CandidateEvent(
                    candidate_id=stable_event_id(
                        "candidate", routing_observation.window_id, fire_type
                    ),
                    window_id=routing_observation.window_id,
                    candidate_type=fire_type,
                    priority=Priority.HIGH,
                    evidence={
                        "temporal_confirmed": True,
                        "backend": fire_evidence[0].backend if fire_evidence else None,
                        "max_confidence": max(
                            (item.confidence for item in fire_evidence), default=0.0
                        ),
                    },
                )
            )
        routing_primary = select_primary_candidate(routing_candidates)
        base_vlm_call = decide_vlm_call(
            routing_observation,
            routing_candidates,
            usable_frame_count=len(frames),
        )
        gate = (
            self.alert_state_store.claim_vlm(admission, base_vlm_call)
            if self.alert_state_store is not None
            else CooldownGateDecision.from_base(admission, base_vlm_call)
        )
        vlm_call = gate.as_vlm_call_decision()
        started = time.perf_counter()
        if not vlm_call.call_vlm:
            no_frames = vlm_call.reason is VLMCallReason.NO_USABLE_FRAMES
            scene = SceneAnalysis(
                summary=(
                    "Không có frame hợp lệ để phân tích."
                    if no_frames
                    else "Không phát hiện chuyển động hoặc đối tượng cần xác minh."
                ),
                alert_level=AlertLevel.LOW,
                degraded=no_frames,
            )
            trace = VLMAnalysisTrace(scene=scene)
        else:
            try:
                if hasattr(self.vlm, "analyze_with_trace"):
                    trace = self.vlm.analyze_with_trace(
                        frames, detections, candidate=routing_primary
                    )
                    scene = trace.scene
                else:
                    scene = (
                        self.vlm.analyze(frames[0], detections)
                        if len(frames) == 1
                        else self.vlm.analyze_sequence(frames, detections)
                    )
                    trace = VLMAnalysisTrace(
                        scene=scene,
                        raw_output=scene.model_dump_json(),
                        raw_output_valid=not scene.degraded,
                        decision=(
                            "uncertain"
                            if scene.degraded
                            else "no"
                            if scene.alert_level is AlertLevel.LOW and not scene.risks
                            else "yes"
                        ),
                    )
            except Exception as exc:
                logger.exception(
                    "VLM verification failed camera=%s window=%s",
                    camera_id,
                    window.window_index,
                )
                scene = SceneAnalysis(
                    summary=f"VLM verification failed: {exc}",
                    alert_level=gate.effective_level,
                    degraded=True,
                )
                trace = VLMAnalysisTrace(
                    scene=scene,
                    raw_output_valid=False,
                    decision="uncertain",
                )
        qwen_ms = (
            (time.perf_counter() - started) * 1000
            if vlm_call.call_vlm
            else 0.0
        )
        observation = routing_observation
        candidates = routing_candidates
        primary = routing_primary
        model_name = getattr(self.vlm, "model", self.vlm.__class__.__name__)
        decision = (
            decision_from_trace(primary, trace, model=model_name, latency_ms=qwen_ms)
            if vlm_call.call_vlm and primary is not None
            else None
        )
        alert = (
            alert_from_decision(
                primary,
                decision,
                camera_id=camera_id,
                recommended_action=scene.recommended_action,
            )
            if primary is not None and decision is not None
            else None
        )
        if self.alert_state_store is not None and vlm_call.call_vlm:
            alert_context = self.alert_state_store.record_result(
                gate=gate,
                end_seconds=window.end_seconds,
                scene=scene,
                alert_event=alert,
                trace_valid=trace.raw_output_valid,
                processing_now=time.monotonic(),
            )
            if alert_context.verification_status is VerificationStatus.FAILED:
                scene = scene.model_copy(
                    update={
                        "alert_level": alert_context.effective_level,
                        "degraded": True,
                    }
                )
                trace = trace.model_copy(update={"scene": scene})
            elif scene.alert_level is not alert_context.effective_level:
                scene = scene.model_copy(
                    update={"alert_level": alert_context.effective_level}
                )
        elif (
            self.alert_state_store is not None
            and admission.recheck
            and vlm_call.reason is VLMCallReason.NO_USABLE_FRAMES
        ):
            failed_context = self.alert_state_store.fail_reserved_admission(
                admission,
                processing_now=time.monotonic(),
            )
            alert_context = failed_context or WindowAlertContext(
                stream_id=stream_id,
                camera_id=camera_id,
                state=admission.state,
                effective_level=admission.effective_level,
                verification_status=VerificationStatus.FAILED,
                source="verification_failed",
                window_start_seconds=window.start_seconds,
                window_end_seconds=window.end_seconds,
                active_alert_id=admission.active_alert_id,
                event_type=admission.event_type,
                recheck=True,
                next_recheck_event_seconds=admission.next_recheck_event_seconds,
            )
            scene = scene.model_copy(
                update={
                    "alert_level": alert_context.effective_level,
                    "degraded": True,
                }
            )
            trace = trace.model_copy(update={"scene": scene})
        else:
            alert_context = WindowAlertContext(
                stream_id=stream_id,
                camera_id=camera_id,
                state=admission.state,
                detected_level=scene.alert_level if vlm_call.call_vlm else None,
                effective_level=scene.alert_level,
                verification_status=(
                    VerificationStatus.VERIFIED
                    if vlm_call.call_vlm and not scene.degraded
                    else VerificationStatus.FAILED
                    if vlm_call.call_vlm
                    else VerificationStatus.NOT_REQUIRED
                ),
                source=(
                    "window_verification" if vlm_call.call_vlm else "policy_skip"
                ),
                window_start_seconds=window.start_seconds,
                window_end_seconds=window.end_seconds,
                active_alert_id=admission.active_alert_id,
                event_type=admission.event_type,
                recheck=admission.recheck,
                next_recheck_event_seconds=admission.next_recheck_event_seconds,
            )
        result = ProcessedVideoWindow(
            scene=scene,
            detections=detections,
            qwen_input=QwenInputSummary(
                frame_indices=[item.frame_index for item in keyframes],
                timestamps_seconds=[round(item.timestamp_seconds, 3) for item in keyframes],
                frame_count=len(frames),
                detection_labels=sorted({item.label for item in detections}),
            ),
            timing=StageTiming(
                motion_ms=motion_ms,
                detector_ms=detector_ms,
                keyframe_ms=keyframe_ms,
                qwen_ms=qwen_ms,
                total_ms=motion_ms + detector_ms + keyframe_ms + qwen_ms,
            ),
            observation=observation,
            candidates=candidates,
            decision=decision,
            alert_event=alert,
            vlm_trace=trace,
            vlm_call=vlm_call,
            alert_context=alert_context,
        )
        if self.artifact_writer is not None:
            self.artifact_writer.write(
                observation=observation,
                candidates=candidates,
                frames=frames,
                prompt=trace.prompt,
                raw_output=trace.raw_output,
                decision=decision,
                alert=alert,
            )
        return result

    @staticmethod
    def _build_suppressed_window(
        window: RawVideoWindow,
        camera_id: str,
        admission: WindowAdmission,
    ) -> ProcessedVideoWindow:
        scene = SceneAnalysis(
            summary=(
                "Cảnh báo đang hoạt động; cửa sổ này không được Qwen xác minh lại."
            ),
            alert_level=admission.effective_level,
            degraded=False,
        )
        reason = admission.reason or VLMCallReason.ACTIVE_ALERT_COOLDOWN
        return ProcessedVideoWindow(
            scene=scene,
            qwen_input=QwenInputSummary(),
            timing=StageTiming(),
            observation=VideoWindowObservation(
                camera_id=camera_id,
                window_id=f"{camera_id}_{window.window_index:06d}",
                start_ms=round(window.start_seconds * 1000),
                end_ms=round(window.end_seconds * 1000),
            ),
            vlm_trace=VLMAnalysisTrace(scene=scene),
            vlm_call=VLMCallDecision(
                call_vlm=False,
                reason=reason,
            ),
            alert_context=WindowAlertContext(
                stream_id=admission.stream_id,
                camera_id=admission.camera_id,
                state=admission.state,
                detected_level=None,
                effective_level=admission.effective_level,
                verification_status=VerificationStatus.SUPPRESSED,
                source=(
                    "inherited_active_alert"
                    if admission.active_alert_id is not None
                    else "camera_vlm_inflight"
                ),
                window_start_seconds=window.start_seconds,
                window_end_seconds=window.end_seconds,
                active_alert_id=admission.active_alert_id,
                event_type=admission.event_type,
                recheck=False,
                next_recheck_event_seconds=admission.next_recheck_event_seconds,
            ),
        )
