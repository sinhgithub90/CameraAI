"""Candidate scoring and temporal keyframe selection for video windows."""
from __future__ import annotations

from collections.abc import Sequence

from .schemas import VideoFrameObservation

VEHICLE_LABELS = {"bicycle", "car", "motorcycle", "bus", "truck"}


def _label_count(
    observation: VideoFrameObservation,
    labels: set[str],
) -> int:
    return sum(item.label in labels for item in observation.detections)


def _observation_change_score(
    previous: VideoFrameObservation | None,
    current: VideoFrameObservation,
) -> float:
    previous_detections = previous.detections if previous is not None else []
    person_delta = abs(
        _label_count(current, {"person"})
        - sum(item.label == "person" for item in previous_detections)
    )
    vehicle_delta = abs(
        _label_count(current, VEHICLE_LABELS)
        - sum(item.label in VEHICLE_LABELS for item in previous_detections)
    )
    previous_labels = {item.label for item in previous_detections}
    current_labels = {item.label for item in current.detections}
    label_changed = float(previous_labels != current_labels)
    return current.motion.score + person_delta + vehicle_delta + label_changed


def score_observations(
    observations: Sequence[VideoFrameObservation],
) -> list[tuple[float, VideoFrameObservation]]:
    """Return observations ranked by motion, detections and temporal context."""
    if not observations:
        return []
    last_index = max(len(observations) - 1, 1)
    scored: list[tuple[float, VideoFrameObservation]] = []
    for position, observation in enumerate(observations):
        detection_score = max(
            (d.confidence for d in observation.detections), default=0.0
        )
        temporal_score = 0.15 if position in (0, last_index) else 0.0
        score = (
            observation.motion.score * 0.6
            + detection_score * 0.3
            + temporal_score
        )
        scored.append((score, observation))
    return sorted(scored, key=lambda item: item[0], reverse=True)


def select_keyframes(
    observations: Sequence[VideoFrameObservation],
    max_keyframes: int = 8,
) -> list[VideoFrameObservation]:
    """Select unique chronological keyframes from one temporal window."""
    if not observations or max_keyframes <= 0:
        return []
    if len(observations) <= max_keyframes:
        return list(observations)

    if max_keyframes == 2:
        change_scores = [
            _observation_change_score(
                observations[position - 1] if position else None,
                observation,
            )
            for position, observation in enumerate(observations)
        ]
        if max(change_scores) == 0:
            return [observations[0], observations[-1]]

        event_position = max(
            range(len(observations)),
            key=lambda position: change_scores[position],
        )
        if event_position == 0:
            return [observations[0], observations[-1]]

        event_frame = observations[event_position]
        separated_context = [
            item
            for item in observations[:event_position]
            if event_frame.timestamp_seconds - item.timestamp_seconds >= 1.0
        ]
        context_frame = (
            separated_context[-1]
            if separated_context
            else observations[0]
        )
        return [context_frame, event_frame]

    selected: dict[int, VideoFrameObservation] = {}

    def add(item: VideoFrameObservation | None) -> None:
        if item is not None:
            selected[item.frame_index] = item

    add(observations[0])
    add(observations[-1])
    motion_items = [item for item in observations if item.motion.motion]
    if motion_items:
        peak_motion = max(motion_items, key=lambda item: item.motion.score)
        add(peak_motion)
        motion_positions = [
            position
            for position, item in enumerate(observations)
            if item.motion.motion
        ]
        first_motion_position = motion_positions[0]
        last_motion_position = motion_positions[-1]
        if first_motion_position > 0:
            add(observations[first_motion_position - 1])
        if last_motion_position + 1 < len(observations):
            add(observations[last_motion_position + 1])

    detected = [item for item in observations if item.detections]
    if detected:
        add(
            max(
                detected,
                key=lambda item: max(d.confidence for d in item.detections),
            )
        )

    if len(selected) < max_keyframes:
        for _, item in score_observations(observations):
            add(item)
            if len(selected) >= max_keyframes:
                break
    elif len(selected) > max_keyframes:
        keep = {observations[0].frame_index, observations[-1].frame_index}
        ranked = [item for _, item in score_observations(observations)]
        for item in ranked:
            if len(keep) >= max_keyframes:
                break
            keep.add(item.frame_index)
        selected = {index: item for index, item in selected.items() if index in keep}

    return sorted(selected.values(), key=lambda item: item.frame_index)[:max_keyframes]
