"""Transport-neutral domain contracts for camera event analysis."""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Iterable, Literal

from pydantic import BaseModel, Field, JsonValue

from .schemas import (
    AlertLevel,
    PipelineResult,
    SecurityDecision,
    VLMResult,
    VideoWindowObservation,
)


class DecisionValue(str, Enum):
    """Allowed verification outcomes."""

    YES = "yes"
    NO = "no"
    UNCERTAIN = "uncertain"


class Priority(str, Enum):
    """Candidate routing priority."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Severity(str, Enum):
    """Confirmed alert severity."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    """Domain review lifecycle, distinct from the VLM execution lifecycle."""

    PENDING_REVIEW = "pending_review"
    CONFIRMED = "confirmed"
    DISMISSED = "dismissed"


class CandidateEvent(BaseModel):
    """An extensible event hypothesis that may require verification."""

    candidate_id: str
    window_id: str
    candidate_type: str
    priority: Priority = Priority.LOW
    evidence: dict[str, JsonValue] = Field(default_factory=dict)
    requires_verification: bool = True


class ModelDecision(BaseModel):
    """One model's explicit decision about a candidate hypothesis."""

    candidate_id: str
    model: str
    decision: DecisionValue
    event_type: str | None = None
    evidence: list[str] = Field(default_factory=list)
    raw_output_valid: bool = False
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    latency_ms: float = Field(default=0.0, ge=0.0)


class AlertEvent(BaseModel):
    """A domain alert created only after candidate verification and policy."""

    alert_id: str
    camera_id: str
    event_type: str
    severity: Severity
    status: AlertStatus = AlertStatus.PENDING_REVIEW
    source_candidate_id: str
    recommended_action: str = ""


def stable_event_id(prefix: str, *context: str | int) -> str:
    """Return ``prefix_<16 hex>`` from canonical JSON and SHA-256.

    The digest input is the UTF-8 encoding of a compact JSON array containing
    the prefix followed by caller-supplied request/window context.
    """

    payload = json.dumps(
        [prefix, *context], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:16]
    return f"{prefix}_{digest}"


_SEVERITY_RANK = {
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


def _legacy_alert_level(severity: Severity) -> AlertLevel:
    if severity in (Severity.HIGH, Severity.CRITICAL):
        return AlertLevel.HIGH
    if severity is Severity.MEDIUM:
        return AlertLevel.MEDIUM
    return AlertLevel.LOW


def project_alert_to_pipeline_result(
    result: PipelineResult,
    *,
    alerts: Iterable[AlertEvent],
    decisions: Iterable[ModelDecision],
) -> PipelineResult:
    """Project the strongest valid affirmative alert into the legacy result."""

    decisions_by_candidate = {
        decision.candidate_id: decision
        for decision in decisions
        if decision.decision is DecisionValue.YES and decision.raw_output_valid
    }
    verified = [
        (alert, decisions_by_candidate[alert.source_candidate_id])
        for alert in alerts
        if alert.status is not AlertStatus.DISMISSED
        and alert.source_candidate_id in decisions_by_candidate
    ]
    if not verified:
        return result.model_copy(deep=True)

    alert, decision = max(verified, key=lambda pair: _SEVERITY_RANK[pair[0].severity])
    evidence = list(decision.evidence)
    return result.model_copy(
        deep=True,
        update={
            "security": SecurityDecision(
                alert_level=_legacy_alert_level(alert.severity),
                risks=evidence,
                recommended_action=alert.recommended_action,
            ),
            "vlm": VLMResult(
                summary=decision.event_type or alert.event_type,
                observations=evidence,
                degraded=False,
                skipped=False,
                status="completed",
            ),
        },
    )


def alert_event_to_store_alert(
    alert: AlertEvent,
    *,
    vlm_status: Literal["pending", "completed", "skipped"] = "pending",
    decision: ModelDecision | None = None,
):
    """Adapt a domain alert to the existing persistence model explicitly."""

    if vlm_status not in {"pending", "completed", "skipped"}:
        raise ValueError("vlm_status must be pending, completed, or skipped")

    from .alert_store import Alert

    evidence = list(decision.evidence) if decision is not None else []
    summary = (decision.event_type or alert.event_type) if decision is not None else ""
    return Alert(
        id=alert.alert_id,
        camera_id=alert.camera_id,
        rule_id=alert.source_candidate_id,
        vlm=VLMResult(
            summary=summary,
            observations=evidence,
            degraded=bool(decision and not decision.raw_output_valid),
            skipped=vlm_status == "skipped",
            status=vlm_status,
        ),
        security=SecurityDecision(
            alert_level=_legacy_alert_level(alert.severity),
            risks=evidence,
            recommended_action=alert.recommended_action,
        ),
    )


__all__ = [
    "AlertEvent",
    "AlertStatus",
    "CandidateEvent",
    "DecisionValue",
    "ModelDecision",
    "Priority",
    "Severity",
    "VideoWindowObservation",
    "alert_event_to_store_alert",
    "project_alert_to_pipeline_result",
    "stable_event_id",
]
