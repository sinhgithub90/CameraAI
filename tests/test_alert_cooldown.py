from camera_ai.alert_cooldown import (
    AlertRuntimePhase,
    CooldownDisposition,
    InMemoryCameraAlertStateStore,
    VerificationStatus,
    WindowDisposition,
)
from camera_ai.event_models import AlertEvent, AlertStatus, Severity
from camera_ai.schemas import AlertLevel, SceneAnalysis
from camera_ai.vlm_policy import VLMCallDecision, VLMCallReason


def call_decision() -> VLMCallDecision:
    return VLMCallDecision(
        call_vlm=True,
        reason=VLMCallReason.CANDIDATE_REQUIRES_VERIFICATION,
        candidate_id="candidate-1",
    )


def red_alert() -> AlertEvent:
    return AlertEvent(
        alert_id="episode-1",
        camera_id="cam-a",
        event_type="traffic_accident",
        severity=Severity.HIGH,
        status=AlertStatus.PENDING_REVIEW,
        source_candidate_id="candidate-1",
    )


def test_red_result_suppresses_only_same_stream_camera_for_sixty_seconds():
    store = InMemoryCameraAlertStateStore()
    first_admission = store.inspect_window(
        stream_id="analysis-a",
        camera_id="cam-a",
        start_seconds=0.0,
        end_seconds=5.0,
        processing_now=100.0,
    )
    first = store.claim_vlm(first_admission, call_decision())
    store.record_result(
        gate=first,
        end_seconds=5.0,
        scene=SceneAnalysis(alert_level=AlertLevel.LOW),
        alert_event=red_alert(),
        trace_valid=True,
        processing_now=105.0,
    )

    blocked = store.inspect_window(
        stream_id="analysis-a",
        camera_id="cam-a",
        start_seconds=5.0,
        end_seconds=10.0,
        processing_now=106.0,
    )
    other = store.inspect_window(
        stream_id="analysis-b",
        camera_id="cam-b",
        start_seconds=5.0,
        end_seconds=10.0,
        processing_now=106.0,
    )

    assert blocked.disposition is WindowDisposition.SUPPRESS
    assert blocked.process_window is False
    assert blocked.active_alert_id == "episode-1"
    assert blocked.effective_level is AlertLevel.HIGH
    assert other.disposition is WindowDisposition.PROCESS_NORMAL


def test_first_window_at_recheck_is_forced_and_red_extends_deadline():
    store = InMemoryCameraAlertStateStore.seeded_red(
        stream_id="analysis-a",
        camera_id="cam-a",
        active_alert_id="episode-1",
        next_recheck_event_seconds=65.0,
    )
    admission = store.inspect_window(
        stream_id="analysis-a",
        camera_id="cam-a",
        start_seconds=65.0,
        end_seconds=70.0,
        processing_now=170.0,
    )
    gate = store.claim_vlm(
        admission,
        VLMCallDecision(
            call_vlm=False,
            reason=VLMCallReason.STATIC_WINDOW,
        ),
    )
    context = store.record_result(
        gate=gate,
        end_seconds=70.0,
        scene=SceneAnalysis(alert_level=AlertLevel.HIGH),
        alert_event=None,
        trace_valid=True,
        processing_now=175.0,
    )

    assert gate.disposition is CooldownDisposition.FORCE_RECHECK
    assert gate.call_vlm is True
    assert context.recheck is True
    assert context.episode_extended is True
    assert store.get("analysis-a", "cam-a").next_recheck_event_seconds == 130.0


def test_recheck_medium_enters_orange_watch_and_low_resolves():
    store = InMemoryCameraAlertStateStore.seeded_red(
        stream_id="analysis-a",
        camera_id="cam-a",
        active_alert_id="episode-1",
        next_recheck_event_seconds=65.0,
    )
    medium_admission = store.inspect_window(
        stream_id="analysis-a",
        camera_id="cam-a",
        start_seconds=65.0,
        end_seconds=70.0,
        processing_now=170.0,
    )
    medium_gate = store.claim_vlm(medium_admission, call_decision())
    store.record_result(
        gate=medium_gate,
        end_seconds=70.0,
        scene=SceneAnalysis(alert_level=AlertLevel.MEDIUM),
        alert_event=None,
        trace_valid=True,
        processing_now=175.0,
    )
    assert store.get("analysis-a", "cam-a").phase is AlertRuntimePhase.ORANGE_WATCH
    assert store.get("analysis-a", "cam-a").next_recheck_event_seconds == 85.0

    low_admission = store.inspect_window(
        stream_id="analysis-a",
        camera_id="cam-a",
        start_seconds=85.0,
        end_seconds=90.0,
        processing_now=190.0,
    )
    low_gate = store.claim_vlm(low_admission, call_decision())
    resolved = store.record_result(
        gate=low_gate,
        end_seconds=90.0,
        scene=SceneAnalysis(alert_level=AlertLevel.LOW),
        alert_event=None,
        trace_valid=True,
        processing_now=195.0,
    )
    assert resolved.episode_resolved is True
    assert store.get("analysis-a", "cam-a").phase is AlertRuntimePhase.NORMAL


def test_failed_recheck_keeps_red_and_uses_bounded_retry_backoff():
    store = InMemoryCameraAlertStateStore.seeded_red(
        stream_id="analysis-a",
        camera_id="cam-a",
        active_alert_id="episode-1",
        next_recheck_event_seconds=65.0,
    )
    retry_times = []
    now = 200.0
    for expected_delay in (15.0, 15.0, 30.0, 60.0, 60.0):
        admission = store.inspect_window(
            stream_id="analysis-a",
            camera_id="cam-a",
            start_seconds=65.0,
            end_seconds=70.0,
            processing_now=now,
        )
        gate = store.claim_vlm(admission, call_decision())
        context = store.record_failure(gate=gate, processing_now=now)
        retry_times.append(
            store.get("analysis-a", "cam-a").retry_due_processing_at
        )
        assert context.effective_level is AlertLevel.HIGH
        assert context.verification_status is VerificationStatus.FAILED
        now += expected_delay

    assert retry_times == [215.0, 230.0, 260.0, 320.0, 380.0]


def test_second_window_is_suppressed_while_same_camera_vlm_is_inflight():
    store = InMemoryCameraAlertStateStore()
    first_admission = store.inspect_window(
        stream_id="analysis-a",
        camera_id="cam-a",
        start_seconds=0.0,
        end_seconds=5.0,
        processing_now=100.0,
    )
    first = store.claim_vlm(first_admission, call_decision())
    second = store.inspect_window(
        stream_id="analysis-a",
        camera_id="cam-a",
        start_seconds=5.0,
        end_seconds=10.0,
        processing_now=101.0,
    )

    assert first.call_vlm is True
    assert second.process_window is False
    assert second.disposition is WindowDisposition.SUPPRESS
    assert second.reason is VLMCallReason.CAMERA_VLM_INFLIGHT
