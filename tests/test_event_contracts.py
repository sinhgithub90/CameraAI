import pytest
from pydantic import ValidationError

from camera_ai.event_models import (
    AlertEvent,
    AlertStatus,
    CandidateEvent,
    DecisionValue,
    ModelDecision,
    Priority,
    Severity,
    alert_event_to_store_alert,
    alert_from_decision,
    project_alert_to_pipeline_result,
    stable_event_id,
)
from camera_ai.schemas import (
    MediaType,
    MotionResult,
    PipelineResult,
    SecurityDecision,
    VLMResult,
    VideoWindowObservation,
)


def test_candidate_is_hypothesis_not_alert():
    candidate = CandidateEvent(
        candidate_id="candidate_001",
        window_id="cam_01_000001",
        candidate_type="person_vehicle_scene",
        priority="medium",
        requires_verification=True,
    )

    assert candidate.candidate_type == "person_vehicle_scene"
    assert candidate.priority is Priority.MEDIUM
    assert candidate.requires_verification is True


def test_candidate_type_remains_an_extensible_free_string():
    candidate = CandidateEvent(
        candidate_id="candidate_custom",
        window_id="cam_01_000001",
        candidate_type="possible_new_site_specific_event",
        priority=Priority.LOW,
    )

    assert candidate.candidate_type == "possible_new_site_specific_event"


def test_invalid_model_decision_value_is_rejected():
    with pytest.raises(ValidationError):
        ModelDecision(candidate_id="c1", model="qwen", decision="maybe")


@pytest.mark.parametrize(
    ("event_type", "expected"),
    [
        ("no_event", Severity.LOW),
        ("person_vehicle_interaction", Severity.LOW),
        ("unknown_event", Severity.LOW),
        ("person_fall", Severity.MEDIUM),
        ("camera_tamper", Severity.MEDIUM),
        ("traffic_accident", Severity.HIGH),
        ("fighting", Severity.HIGH),
        ("fire_smoke", Severity.HIGH),
        ("future_event", Severity.LOW),
    ],
)
def test_alert_severity_comes_from_validated_event_type(event_type, expected):
    candidate = CandidateEvent(
        candidate_id="candidate_001",
        window_id="window_001",
        candidate_type="person_vehicle_scene",
        priority=Priority.CRITICAL,
    )
    decision = ModelDecision(
        candidate_id=candidate.candidate_id,
        model="qwen",
        decision=DecisionValue.YES,
        event_type=event_type,
        raw_output_valid=True,
    )

    alert = alert_from_decision(candidate, decision, camera_id="cam_01")

    assert alert is not None
    assert alert.severity is expected


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        (CandidateEvent, "priority", "urgent"),
        (AlertEvent, "severity", "urgent"),
        (AlertEvent, "status", "queued"),
    ],
)
def test_invalid_domain_enum_values_are_rejected(model, field, value):
    values = {
        "candidate_id": "candidate_001",
        "window_id": "cam_01_000001",
        "candidate_type": "possible_activity",
        "priority": Priority.LOW,
        "alert_id": "alert_001",
        "camera_id": "cam_01",
        "event_type": "activity",
        "severity": Severity.LOW,
        "status": AlertStatus.PENDING_REVIEW,
        "source_candidate_id": "candidate_001",
    }
    values[field] = value

    with pytest.raises(ValidationError):
        model(**values)


def test_window_observation_uses_existing_motion_contract():
    observation = VideoWindowObservation(
        camera_id="cam_01",
        window_id="cam_01_000001",
        start_ms=0,
        end_ms=5_000,
        motion=MotionResult(motion=True, score=0.72),
        selected_frames=[12, 20],
    )

    assert observation.motion.score == 0.72
    assert observation.selected_frames == [12, 20]
    assert observation.detections == []


def test_stable_event_id_uses_canonical_sha256_context():
    first = stable_event_id("window", "request_7", "cam_01", 3)
    repeated = stable_event_id("window", "request_7", "cam_01", 3)

    assert first == "window_19453c0024c8c652"
    assert repeated == first
    assert stable_event_id("window", "request_7", "cam_01", 4) != first


def _pipeline_result() -> PipelineResult:
    return PipelineResult(
        request_id="request_7",
        media_type=MediaType.VIDEO,
        camera_id="cam_01",
        vlm=VLMResult(summary="legacy", status="completed"),
        security=SecurityDecision(),
    )


def test_projection_selects_highest_severity_verified_affirmative_alert():
    low_decision = ModelDecision(
        candidate_id="candidate_low",
        model="qwen",
        decision=DecisionValue.YES,
        event_type="person_activity",
        evidence=["person remained in the zone"],
        raw_output_valid=True,
        confidence=0.81,
        latency_ms=120.5,
    )
    high_decision = ModelDecision(
        candidate_id="candidate_high",
        model="qwen",
        decision=DecisionValue.YES,
        event_type="traffic_incident",
        evidence=["vehicle positions changed abruptly"],
        raw_output_valid=True,
        latency_ms=4183.7,
    )
    result = project_alert_to_pipeline_result(
        _pipeline_result(),
        alerts=[
            AlertEvent(
                alert_id="alert_low",
                camera_id="cam_01",
                event_type="person_activity",
                severity=Severity.LOW,
                status=AlertStatus.CONFIRMED,
                source_candidate_id="candidate_low",
            ),
            AlertEvent(
                alert_id="alert_high",
                camera_id="cam_01",
                event_type="traffic_incident",
                severity=Severity.HIGH,
                status=AlertStatus.PENDING_REVIEW,
                source_candidate_id="candidate_high",
                recommended_action="Review the incident footage.",
            ),
        ],
        decisions=[low_decision, high_decision],
    )

    assert result.request_id == "request_7"
    assert result.security.alert_level.value == "high"
    assert result.security.risks == ["vehicle positions changed abruptly"]
    assert result.security.recommended_action == "Review the incident footage."
    assert result.vlm.summary == "traffic_incident"
    assert result.vlm.observations == ["vehicle positions changed abruptly"]
    assert result.vlm.status == "completed"


@pytest.mark.parametrize(
    "decision",
    [
        ModelDecision(
            candidate_id="candidate_001",
            model="qwen",
            decision=DecisionValue.NO,
            raw_output_valid=True,
        ),
        ModelDecision(
            candidate_id="candidate_001",
            model="qwen",
            decision=DecisionValue.YES,
            raw_output_valid=False,
        ),
        ModelDecision(
            candidate_id="candidate_001",
            model="qwen",
            decision=DecisionValue.UNCERTAIN,
            raw_output_valid=True,
        ),
    ],
)
def test_projection_ignores_nonaffirmative_or_invalid_decisions(decision):
    original = _pipeline_result()
    projected = project_alert_to_pipeline_result(
        original,
        alerts=[
            AlertEvent(
                alert_id="alert_001",
                camera_id="cam_01",
                event_type="traffic_incident",
                severity=Severity.HIGH,
                status=AlertStatus.PENDING_REVIEW,
                source_candidate_id="candidate_001",
            )
        ],
        decisions=[decision],
    )

    assert projected == original


def test_projection_requires_raw_output_to_be_explicitly_valid():
    original = _pipeline_result()
    projected = project_alert_to_pipeline_result(
        original,
        alerts=[
            AlertEvent(
                alert_id="alert_001",
                camera_id="cam_01",
                event_type="traffic_incident",
                severity=Severity.HIGH,
                source_candidate_id="candidate_001",
            )
        ],
        decisions=[
            ModelDecision(
                candidate_id="candidate_001",
                model="qwen",
                decision=DecisionValue.YES,
            )
        ],
    )

    assert projected == original


@pytest.mark.parametrize("vlm_status", ["pending", "completed", "skipped"])
def test_alert_store_adapter_preserves_vlm_lifecycle_status(vlm_status):
    stored = alert_event_to_store_alert(
        AlertEvent(
            alert_id="alert_001",
            camera_id="cam_01",
            event_type="traffic_incident",
            severity=Severity.HIGH,
            status=AlertStatus.PENDING_REVIEW,
            source_candidate_id="candidate_001",
        ),
        vlm_status=vlm_status,
    )

    assert stored.id == "alert_001"
    assert stored.camera_id == "cam_01"
    assert stored.rule_id == "candidate_001"
    assert stored.vlm.status == vlm_status
    assert stored.vlm.skipped is (vlm_status == "skipped")


def test_alert_store_adapter_rejects_unknown_vlm_lifecycle_status():
    alert = AlertEvent(
        alert_id="alert_001",
        camera_id="cam_01",
        event_type="traffic_incident",
        severity=Severity.HIGH,
        status=AlertStatus.PENDING_REVIEW,
        source_candidate_id="candidate_001",
    )

    with pytest.raises(ValueError, match="pending, completed, or skipped"):
        alert_event_to_store_alert(alert, vlm_status="queued")
