"""Aggregate per-frame detections without pretending to track object identity."""
from __future__ import annotations

from collections.abc import Sequence

from .router import VEHICLE_LABELS, detections_are_near
from .schemas import VideoFrameObservation, WindowDetectionAggregate


def aggregate_window_detections(
    observations: Sequence[VideoFrameObservation],
) -> WindowDetectionAggregate:
    person_peak = vehicle_peak = 0
    person_frames = vehicle_frames = proximity_frames = 0

    for observation in observations:
        people = [item for item in observation.detections if item.label == "person"]
        vehicles = [
            item for item in observation.detections if item.label in VEHICLE_LABELS
        ]
        person_peak = max(person_peak, len(people))
        vehicle_peak = max(vehicle_peak, len(vehicles))
        person_frames += bool(people)
        vehicle_frames += bool(vehicles)
        proximity_frames += bool(
            people
            and vehicles
            and any(
                detections_are_near(person, vehicle)
                for person in people
                for vehicle in vehicles
            )
        )

    return WindowDetectionAggregate(
        person_peak_count=person_peak,
        vehicle_peak_count=vehicle_peak,
        person_detection_frames=person_frames,
        vehicle_detection_frames=vehicle_frames,
        proximity_frame_count=proximity_frames,
        sampled_frame_count=len(observations),
    )


__all__ = ["aggregate_window_detections"]
