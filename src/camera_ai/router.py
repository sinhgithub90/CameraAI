"""Conservative routing from observations to event hypotheses."""
from __future__ import annotations

from .event_models import CandidateEvent, Priority, stable_event_id
from .schemas import Detection, VideoWindowObservation

VEHICLE_LABELS = {"bicycle", "car", "motorcycle", "bus", "truck"}


def detections_are_near(left: Detection, right: Detection) -> bool:
    lx1, ly1, lx2, ly2 = left.bbox
    rx1, ry1, rx2, ry2 = right.bbox
    gap_x = max(0.0, max(lx1, rx1) - min(lx2, rx2))
    gap_y = max(0.0, max(ly1, ry1) - min(ly2, ry2))
    scale = max(lx2 - lx1, ly2 - ly1, rx2 - rx1, ry2 - ry1, 1.0)
    return (gap_x * gap_x + gap_y * gap_y) ** 0.5 <= scale


def _candidate(
    observation: VideoWindowObservation,
    candidate_type: str,
    priority: Priority,
    evidence: dict,
) -> CandidateEvent:
    return CandidateEvent(
        candidate_id=stable_event_id("candidate", observation.window_id, candidate_type),
        window_id=observation.window_id,
        candidate_type=candidate_type,
        priority=priority,
        evidence=evidence,
        requires_verification=True,
    )


def route_observation(observation: VideoWindowObservation) -> list[CandidateEvent]:
    aggregate = observation.detection_aggregate
    if aggregate.sampled_frame_count == 0 and observation.detections:
        people = [item for item in observation.detections if item.label == "person"]
        vehicles = [
            item for item in observation.detections if item.label in VEHICLE_LABELS
        ]
        aggregate = aggregate.model_copy(
            update={
                "person_peak_count": len(people),
                "vehicle_peak_count": len(vehicles),
                "person_detection_frames": int(bool(people)),
                "vehicle_detection_frames": int(bool(vehicles)),
                "proximity_frame_count": int(
                    any(
                        detections_are_near(person, vehicle)
                        for person in people
                        for vehicle in vehicles
                    )
                ),
                "sampled_frame_count": 1,
            }
        )
    base = {
        **aggregate.model_dump(),
        "motion_peak": observation.motion.score,
    }
    if aggregate.person_peak_count and aggregate.vehicle_peak_count:
        return [
            _candidate(
                observation,
                "person_vehicle_scene",
                Priority.MEDIUM if aggregate.proximity_frame_count else Priority.LOW,
                base,
            )
        ]
    if aggregate.person_peak_count >= 2 and observation.motion.score >= 0.6:
        return [
            _candidate(
                observation, "multi_person_scene", Priority.MEDIUM, base
            )
        ]
    if aggregate.person_peak_count:
        return [_candidate(observation, "person_scene", Priority.LOW, base)]
    if aggregate.vehicle_peak_count:
        return [_candidate(observation, "vehicle_scene", Priority.LOW, base)]
    if observation.motion.motion:
        return [_candidate(observation, "unexplained_motion", Priority.LOW, base)]
    return []
