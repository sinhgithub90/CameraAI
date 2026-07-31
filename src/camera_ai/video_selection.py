"""Candidate scoring and temporal keyframe selection for video windows."""
from __future__ import annotations

from collections.abc import Sequence

from .schemas import VideoFrameObservation


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
        first_motion_position = observations.index(motion_items[0])
        last_motion_position = observations.index(motion_items[-1])
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
