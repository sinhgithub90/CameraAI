"""Deterministic policy for deciding whether a video window needs VLM."""
from __future__ import annotations

from collections.abc import Sequence
from enum import Enum

from pydantic import BaseModel

from .event_models import CandidateEvent, Priority, select_primary_candidate
from .schemas import VideoWindowObservation


class VLMCallReason(str, Enum):
    STATIC_WINDOW = "static_window"
    NO_USABLE_FRAMES = "no_usable_frames"
    CANDIDATE_REQUIRES_VERIFICATION = "candidate_requires_verification"


class VLMCallDecision(BaseModel):
    call_vlm: bool
    reason: VLMCallReason
    priority: Priority = Priority.LOW
    candidate_id: str | None = None


def decide_vlm_call(
    observation: VideoWindowObservation,
    candidates: Sequence[CandidateEvent],
    *,
    usable_frame_count: int,
) -> VLMCallDecision:
    """Return a conservative one-call-or-skip decision for one window."""
    del observation  # Reserved for richer evidence-based policies.
    primary = select_primary_candidate(candidates)
    if usable_frame_count == 0:
        return VLMCallDecision(
            call_vlm=False,
            reason=VLMCallReason.NO_USABLE_FRAMES,
        )
    if primary is None:
        return VLMCallDecision(
            call_vlm=False,
            reason=VLMCallReason.STATIC_WINDOW,
        )
    return VLMCallDecision(
        call_vlm=True,
        reason=VLMCallReason.CANDIDATE_REQUIRES_VERIFICATION,
        priority=primary.priority,
        candidate_id=primary.candidate_id,
    )


__all__ = [
    "VLMCallDecision",
    "VLMCallReason",
    "decide_vlm_call",
]
