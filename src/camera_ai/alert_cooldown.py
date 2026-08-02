"""Per-stream camera alert state used to suppress redundant video inference."""
from __future__ import annotations

from enum import Enum
from threading import RLock
from typing import Protocol

from pydantic import BaseModel

from .event_models import AlertEvent, Priority, Severity
from .schemas import AlertLevel, SceneAnalysis
from .vlm_policy import VLMCallDecision, VLMCallReason

RED_COOLDOWN_SECONDS = 60.0
ORANGE_RECHECK_SECONDS = 15.0
RETRY_DELAYS_SECONDS = (15.0, 15.0, 30.0, 60.0)


class AlertRuntimePhase(str, Enum):
    NORMAL = "normal"
    ALERT_ACTIVE = "alert_active"
    ORANGE_WATCH = "orange_watch"
    ALERT_ACTIVE_UNVERIFIED = "alert_active_unverified"


class VerificationStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    VERIFIED = "verified"
    SUPPRESSED = "suppressed"
    FAILED = "failed"


class CooldownDisposition(str, Enum):
    USE_BASE_POLICY = "use_base_policy"
    SUPPRESS = "suppress"
    FORCE_RECHECK = "force_recheck"


class WindowDisposition(str, Enum):
    PROCESS_NORMAL = "process_normal"
    SUPPRESS = "suppress"
    PROCESS_RECHECK = "process_recheck"


class CameraAlertRuntime(BaseModel):
    stream_id: str
    camera_id: str
    phase: AlertRuntimePhase = AlertRuntimePhase.NORMAL
    current_level: AlertLevel = AlertLevel.LOW
    active_alert_id: str | None = None
    event_type: str | None = None
    next_recheck_event_seconds: float | None = None
    retry_due_processing_at: float | None = None
    verification_status: VerificationStatus = VerificationStatus.NOT_REQUIRED
    vlm_inflight: bool = False
    recheck_reserved: bool = False
    retry_count: int = 0
    state_version: int = 0


class WindowAdmission(BaseModel):
    stream_id: str
    camera_id: str
    start_seconds: float
    end_seconds: float
    disposition: WindowDisposition
    process_window: bool
    state: AlertRuntimePhase = AlertRuntimePhase.NORMAL
    effective_level: AlertLevel = AlertLevel.LOW
    active_alert_id: str | None = None
    event_type: str | None = None
    reason: VLMCallReason | None = None
    recheck: bool = False
    next_recheck_event_seconds: float | None = None
    reservation_version: int | None = None

    @classmethod
    def normal(
        cls,
        *,
        stream_id: str,
        camera_id: str,
        start_seconds: float,
        end_seconds: float,
    ) -> WindowAdmission:
        return cls(
            stream_id=stream_id,
            camera_id=camera_id,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            disposition=WindowDisposition.PROCESS_NORMAL,
            process_window=True,
        )


class CooldownGateDecision(BaseModel):
    stream_id: str
    camera_id: str
    start_seconds: float
    end_seconds: float
    disposition: CooldownDisposition
    call_vlm: bool
    reason: VLMCallReason
    priority: Priority = Priority.LOW
    candidate_id: str | None = None
    effective_level: AlertLevel = AlertLevel.LOW
    active_alert_id: str | None = None
    event_type: str | None = None
    recheck: bool = False

    def as_vlm_call_decision(self) -> VLMCallDecision:
        return VLMCallDecision(
            call_vlm=self.call_vlm,
            reason=self.reason,
            priority=self.priority,
            candidate_id=self.candidate_id,
        )

    @classmethod
    def from_base(
        cls,
        admission: WindowAdmission,
        base_decision: VLMCallDecision,
    ) -> CooldownGateDecision:
        return cls(
            stream_id=admission.stream_id,
            camera_id=admission.camera_id,
            start_seconds=admission.start_seconds,
            end_seconds=admission.end_seconds,
            disposition=CooldownDisposition.USE_BASE_POLICY,
            call_vlm=base_decision.call_vlm,
            reason=base_decision.reason,
            priority=base_decision.priority,
            candidate_id=base_decision.candidate_id,
            effective_level=admission.effective_level,
            active_alert_id=admission.active_alert_id,
            event_type=admission.event_type,
            recheck=admission.recheck,
        )


class WindowAlertContext(BaseModel):
    stream_id: str = "default"
    camera_id: str = "unknown"
    state: AlertRuntimePhase = AlertRuntimePhase.NORMAL
    detected_level: AlertLevel | None = None
    effective_level: AlertLevel = AlertLevel.LOW
    verification_status: VerificationStatus = VerificationStatus.NOT_REQUIRED
    source: str = "none"
    window_start_seconds: float = 0.0
    window_end_seconds: float = 0.0
    active_alert_id: str | None = None
    event_type: str | None = None
    recheck: bool = False
    episode_created: bool = False
    episode_extended: bool = False
    episode_resolved: bool = False
    next_recheck_event_seconds: float | None = None


class CameraAlertStateStore(Protocol):
    def admit_before_queue(
        self,
        *,
        stream_id: str,
        camera_id: str,
        start_seconds: float,
        end_seconds: float,
        processing_now: float,
    ) -> WindowAdmission: ...

    def inspect_window(
        self,
        *,
        stream_id: str,
        camera_id: str,
        start_seconds: float,
        end_seconds: float,
        processing_now: float,
    ) -> WindowAdmission: ...

    def claim_vlm(
        self,
        admission: WindowAdmission,
        base_decision: VLMCallDecision,
    ) -> CooldownGateDecision: ...

    def record_result(
        self,
        *,
        gate: CooldownGateDecision,
        end_seconds: float,
        scene: SceneAnalysis,
        alert_event: AlertEvent | None,
        trace_valid: bool,
        processing_now: float,
    ) -> WindowAlertContext: ...

    def record_failure(
        self,
        *,
        gate: CooldownGateDecision,
        processing_now: float,
    ) -> WindowAlertContext: ...

    def fail_reserved_admission(
        self,
        admission: WindowAdmission,
        *,
        processing_now: float,
    ) -> WindowAlertContext | None: ...

    def get(self, stream_id: str, camera_id: str) -> CameraAlertRuntime: ...


class InMemoryCameraAlertStateStore:
    def __init__(self) -> None:
        self._states: dict[tuple[str, str], CameraAlertRuntime] = {}
        self._lock = RLock()

    @classmethod
    def seeded_red(
        cls,
        *,
        stream_id: str,
        camera_id: str,
        active_alert_id: str,
        next_recheck_event_seconds: float,
    ) -> InMemoryCameraAlertStateStore:
        store = cls()
        with store._lock:
            store._states[(stream_id, camera_id)] = CameraAlertRuntime(
                stream_id=stream_id,
                camera_id=camera_id,
                phase=AlertRuntimePhase.ALERT_ACTIVE,
                current_level=AlertLevel.HIGH,
                active_alert_id=active_alert_id,
                event_type="traffic_accident",
                next_recheck_event_seconds=next_recheck_event_seconds,
                verification_status=VerificationStatus.VERIFIED,
            )
        return store

    def _runtime(self, stream_id: str, camera_id: str) -> CameraAlertRuntime:
        key = (stream_id, camera_id)
        runtime = self._states.get(key)
        if runtime is None:
            runtime = CameraAlertRuntime(stream_id=stream_id, camera_id=camera_id)
            self._states[key] = runtime
        return runtime

    def get(self, stream_id: str, camera_id: str) -> CameraAlertRuntime:
        with self._lock:
            return self._runtime(stream_id, camera_id).model_copy(deep=True)

    def inspect_window(
        self,
        *,
        stream_id: str,
        camera_id: str,
        start_seconds: float,
        end_seconds: float,
        processing_now: float,
    ) -> WindowAdmission:
        with self._lock:
            runtime = self._runtime(stream_id, camera_id)
            if runtime.vlm_inflight:
                return self._suppressed_admission(
                    runtime,
                    start_seconds,
                    end_seconds,
                    VLMCallReason.CAMERA_VLM_INFLIGHT,
                )
            if runtime.phase is AlertRuntimePhase.NORMAL:
                return WindowAdmission.normal(
                    stream_id=stream_id,
                    camera_id=camera_id,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                )
            if runtime.phase is AlertRuntimePhase.ALERT_ACTIVE_UNVERIFIED:
                due = runtime.retry_due_processing_at
                if due is not None and processing_now < due:
                    return self._suppressed_admission(
                        runtime,
                        start_seconds,
                        end_seconds,
                        VLMCallReason.ACTIVE_ALERT_COOLDOWN,
                    )
                return self._recheck_admission(runtime, start_seconds, end_seconds)
            deadline = runtime.next_recheck_event_seconds
            if deadline is not None and start_seconds >= deadline:
                return self._recheck_admission(runtime, start_seconds, end_seconds)
            return self._suppressed_admission(
                runtime,
                start_seconds,
                end_seconds,
                VLMCallReason.ACTIVE_ALERT_COOLDOWN,
            )

    def admit_before_queue(
        self,
        *,
        stream_id: str,
        camera_id: str,
        start_seconds: float,
        end_seconds: float,
        processing_now: float,
    ) -> WindowAdmission:
        """Atomically admit producer work without dropping normal in-flight windows."""
        with self._lock:
            runtime = self._runtime(stream_id, camera_id)
            if runtime.phase is AlertRuntimePhase.NORMAL:
                return WindowAdmission.normal(
                    stream_id=stream_id,
                    camera_id=camera_id,
                    start_seconds=start_seconds,
                    end_seconds=end_seconds,
                )

            if runtime.phase is AlertRuntimePhase.ALERT_ACTIVE_UNVERIFIED:
                due = runtime.retry_due_processing_at
                recheck_due = due is None or processing_now >= due
            else:
                deadline = runtime.next_recheck_event_seconds
                recheck_due = deadline is not None and start_seconds >= deadline

            if not recheck_due or runtime.recheck_reserved or runtime.vlm_inflight:
                return self._suppressed_admission(
                    runtime,
                    start_seconds,
                    end_seconds,
                    VLMCallReason.ACTIVE_ALERT_COOLDOWN,
                )

            runtime.recheck_reserved = True
            runtime.state_version += 1
            admission = self._recheck_admission(runtime, start_seconds, end_seconds)
            return admission.model_copy(
                update={"reservation_version": runtime.state_version}
            )

    @staticmethod
    def _suppressed_admission(
        runtime: CameraAlertRuntime,
        start_seconds: float,
        end_seconds: float,
        reason: VLMCallReason,
    ) -> WindowAdmission:
        return WindowAdmission(
            stream_id=runtime.stream_id,
            camera_id=runtime.camera_id,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            disposition=WindowDisposition.SUPPRESS,
            process_window=False,
            state=runtime.phase,
            effective_level=runtime.current_level,
            active_alert_id=runtime.active_alert_id,
            event_type=runtime.event_type,
            reason=reason,
            next_recheck_event_seconds=runtime.next_recheck_event_seconds,
        )

    @staticmethod
    def _recheck_admission(
        runtime: CameraAlertRuntime,
        start_seconds: float,
        end_seconds: float,
    ) -> WindowAdmission:
        return WindowAdmission(
            stream_id=runtime.stream_id,
            camera_id=runtime.camera_id,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
            disposition=WindowDisposition.PROCESS_RECHECK,
            process_window=True,
            state=runtime.phase,
            effective_level=runtime.current_level,
            active_alert_id=runtime.active_alert_id,
            event_type=runtime.event_type,
            recheck=True,
            next_recheck_event_seconds=runtime.next_recheck_event_seconds,
        )

    def claim_vlm(
        self,
        admission: WindowAdmission,
        base_decision: VLMCallDecision,
    ) -> CooldownGateDecision:
        with self._lock:
            runtime = self._runtime(admission.stream_id, admission.camera_id)
            if not admission.process_window or runtime.vlm_inflight:
                reason = (
                    admission.reason
                    if not admission.process_window and admission.reason is not None
                    else VLMCallReason.CAMERA_VLM_INFLIGHT
                )
                return self._gate(
                    admission,
                    base_decision,
                    disposition=CooldownDisposition.SUPPRESS,
                    call_vlm=False,
                    reason=reason,
                )
            if admission.recheck:
                if base_decision.reason is VLMCallReason.NO_USABLE_FRAMES:
                    return self._gate(
                        admission,
                        base_decision,
                        disposition=CooldownDisposition.SUPPRESS,
                        call_vlm=False,
                        reason=VLMCallReason.NO_USABLE_FRAMES,
                    )
                runtime.vlm_inflight = True
                runtime.state_version += 1
                return self._gate(
                    admission,
                    base_decision,
                    disposition=CooldownDisposition.FORCE_RECHECK,
                    call_vlm=True,
                    reason=VLMCallReason.ACTIVE_ALERT_RECHECK,
                )
            if base_decision.call_vlm:
                runtime.vlm_inflight = True
                runtime.state_version += 1
            return self._gate(
                admission,
                base_decision,
                disposition=CooldownDisposition.USE_BASE_POLICY,
                call_vlm=base_decision.call_vlm,
                reason=base_decision.reason,
            )

    def fail_reserved_admission(
        self,
        admission: WindowAdmission,
        *,
        processing_now: float,
    ) -> WindowAlertContext | None:
        """Fail only the still-current producer reservation token."""
        with self._lock:
            runtime = self._runtime(admission.stream_id, admission.camera_id)
            if (
                not admission.recheck
                or admission.reservation_version is None
                or admission.reservation_version != runtime.state_version
                or not runtime.recheck_reserved
            ):
                return None
            gate = CooldownGateDecision(
                stream_id=admission.stream_id,
                camera_id=admission.camera_id,
                start_seconds=admission.start_seconds,
                end_seconds=admission.end_seconds,
                disposition=CooldownDisposition.FORCE_RECHECK,
                call_vlm=False,
                reason=VLMCallReason.ACTIVE_ALERT_RECHECK,
                effective_level=admission.effective_level,
                active_alert_id=admission.active_alert_id,
                event_type=admission.event_type,
                recheck=True,
            )
            return self.record_failure(gate=gate, processing_now=processing_now)

    @staticmethod
    def _gate(
        admission: WindowAdmission,
        base_decision: VLMCallDecision,
        *,
        disposition: CooldownDisposition,
        call_vlm: bool,
        reason: VLMCallReason,
    ) -> CooldownGateDecision:
        return CooldownGateDecision(
            stream_id=admission.stream_id,
            camera_id=admission.camera_id,
            start_seconds=admission.start_seconds,
            end_seconds=admission.end_seconds,
            disposition=disposition,
            call_vlm=call_vlm,
            reason=reason,
            priority=base_decision.priority,
            candidate_id=base_decision.candidate_id,
            effective_level=admission.effective_level,
            active_alert_id=admission.active_alert_id,
            event_type=admission.event_type,
            recheck=admission.recheck,
        )

    def record_result(
        self,
        *,
        gate: CooldownGateDecision,
        end_seconds: float,
        scene: SceneAnalysis,
        alert_event: AlertEvent | None,
        trace_valid: bool,
        processing_now: float,
    ) -> WindowAlertContext:
        if not trace_valid or scene.degraded:
            return self.record_failure(gate=gate, processing_now=processing_now)
        with self._lock:
            runtime = self._runtime(gate.stream_id, gate.camera_id)
            runtime.vlm_inflight = False
            runtime.recheck_reserved = False
            detected = self._verified_level(scene, alert_event)
            had_episode = runtime.active_alert_id is not None
            created = extended = resolved = False
            if detected is AlertLevel.HIGH and (had_episode or alert_event is not None):
                created = not had_episode
                extended = had_episode and gate.recheck
                runtime.phase = AlertRuntimePhase.ALERT_ACTIVE
                runtime.current_level = AlertLevel.HIGH
                runtime.active_alert_id = runtime.active_alert_id or alert_event.alert_id
                runtime.event_type = (
                    alert_event.event_type if alert_event is not None else runtime.event_type
                )
                runtime.next_recheck_event_seconds = end_seconds + RED_COOLDOWN_SECONDS
                runtime.retry_due_processing_at = None
                runtime.retry_count = 0
            elif detected is AlertLevel.MEDIUM and had_episode:
                runtime.phase = AlertRuntimePhase.ORANGE_WATCH
                runtime.current_level = AlertLevel.MEDIUM
                runtime.next_recheck_event_seconds = end_seconds + ORANGE_RECHECK_SECONDS
                runtime.retry_due_processing_at = None
                runtime.retry_count = 0
            elif detected is AlertLevel.LOW and had_episode:
                resolved = True
                runtime.phase = AlertRuntimePhase.NORMAL
                runtime.current_level = AlertLevel.LOW
                runtime.active_alert_id = None
                runtime.event_type = None
                runtime.next_recheck_event_seconds = None
                runtime.retry_due_processing_at = None
                runtime.retry_count = 0
            else:
                runtime.phase = AlertRuntimePhase.NORMAL
                runtime.current_level = AlertLevel.LOW
            runtime.verification_status = VerificationStatus.VERIFIED
            runtime.state_version += 1
            return self._context(
                gate,
                runtime,
                detected_level=detected,
                effective_level=detected if not had_episode and not created else runtime.current_level,
                verification_status=VerificationStatus.VERIFIED,
                source="window_verification",
                episode_created=created,
                episode_extended=extended,
                episode_resolved=resolved,
                active_alert_id=(
                    gate.active_alert_id if resolved else runtime.active_alert_id
                ),
            )

    def record_failure(
        self,
        *,
        gate: CooldownGateDecision,
        processing_now: float,
    ) -> WindowAlertContext:
        with self._lock:
            runtime = self._runtime(gate.stream_id, gate.camera_id)
            runtime.vlm_inflight = False
            runtime.recheck_reserved = False
            if runtime.active_alert_id is not None:
                runtime.phase = AlertRuntimePhase.ALERT_ACTIVE_UNVERIFIED
                runtime.verification_status = VerificationStatus.FAILED
                runtime.retry_count += 1
                delay = RETRY_DELAYS_SECONDS[
                    min(runtime.retry_count - 1, len(RETRY_DELAYS_SECONDS) - 1)
                ]
                runtime.retry_due_processing_at = processing_now + delay
            else:
                runtime.phase = AlertRuntimePhase.NORMAL
                runtime.current_level = AlertLevel.LOW
                runtime.verification_status = VerificationStatus.FAILED
            runtime.state_version += 1
            return self._context(
                gate,
                runtime,
                detected_level=None,
                effective_level=runtime.current_level,
                verification_status=VerificationStatus.FAILED,
                source=(
                    "inherited_active_alert"
                    if runtime.active_alert_id is not None
                    else "verification_failed"
                ),
                active_alert_id=runtime.active_alert_id,
            )

    @staticmethod
    def _verified_level(
        scene: SceneAnalysis,
        alert_event: AlertEvent | None,
    ) -> AlertLevel:
        if alert_event is None:
            return scene.alert_level
        if alert_event.severity in {Severity.HIGH, Severity.CRITICAL}:
            return AlertLevel.HIGH
        if alert_event.severity is Severity.MEDIUM:
            return AlertLevel.MEDIUM
        return AlertLevel.LOW

    @staticmethod
    def _context(
        gate: CooldownGateDecision,
        runtime: CameraAlertRuntime,
        *,
        detected_level: AlertLevel | None,
        effective_level: AlertLevel,
        verification_status: VerificationStatus,
        source: str,
        active_alert_id: str | None,
        episode_created: bool = False,
        episode_extended: bool = False,
        episode_resolved: bool = False,
    ) -> WindowAlertContext:
        return WindowAlertContext(
            stream_id=gate.stream_id,
            camera_id=gate.camera_id,
            state=runtime.phase,
            detected_level=detected_level,
            effective_level=effective_level,
            verification_status=verification_status,
            source=source,
            window_start_seconds=gate.start_seconds,
            window_end_seconds=gate.end_seconds,
            active_alert_id=active_alert_id,
            event_type=runtime.event_type or gate.event_type,
            recheck=gate.recheck,
            episode_created=episode_created,
            episode_extended=episode_extended,
            episode_resolved=episode_resolved,
            next_recheck_event_seconds=runtime.next_recheck_event_seconds,
        )


__all__ = [
    "AlertRuntimePhase",
    "CameraAlertRuntime",
    "CameraAlertStateStore",
    "CooldownDisposition",
    "CooldownGateDecision",
    "InMemoryCameraAlertStateStore",
    "ORANGE_RECHECK_SECONDS",
    "RED_COOLDOWN_SECONDS",
    "RETRY_DELAYS_SECONDS",
    "VerificationStatus",
    "WindowAdmission",
    "WindowAlertContext",
    "WindowDisposition",
]
