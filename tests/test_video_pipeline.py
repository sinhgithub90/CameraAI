from __future__ import annotations

import numpy as np

from camera_ai.schemas import MotionResult, VideoFrameObservation
from camera_ai.detectors.motion import MotionDetector
from camera_ai.schemas import Detection
from camera_ai.video_selection import select_keyframes


def test_motion_result_and_video_observation_contracts():
    motion = MotionResult(motion=True, changed_ratio=0.2, score=0.8)
    item = VideoFrameObservation(
        frame_index=3,
        timestamp_seconds=0.6,
        motion=motion,
        frame=np.zeros((4, 4, 3), dtype=np.uint8),
    )
    assert item.motion.score == 0.8
    assert item.frame_index == 3


def test_motion_detector_first_and_static_frames_are_calm():
    detector = MotionDetector()
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    assert detector.compare(frame).motion is False
    assert detector.compare(frame.copy()).motion is False


def test_motion_detector_detects_changed_region():
    detector = MotionDetector(threshold=0.01)
    first = np.zeros((80, 80, 3), dtype=np.uint8)
    second = first.copy()
    second[20:50, 25:60] = 255
    detector.compare(first)
    result = detector.compare(second)
    assert result.motion is True
    assert result.changed_ratio > 0
    assert result.regions


def make_observations(
    count: int,
    motion_indices: set[int] | None = None,
    detection_indices: set[int] | None = None,
) -> list[VideoFrameObservation]:
    motion_indices = motion_indices or set()
    detection_indices = detection_indices or set()
    return [
        VideoFrameObservation(
            frame_index=index,
            timestamp_seconds=index / 5,
            motion=MotionResult(
                motion=index in motion_indices,
                changed_ratio=0.4 if index in motion_indices else 0.0,
                score=0.9 if index in motion_indices else 0.0,
            ),
            detections=(
                [Detection(label="person", confidence=0.95, bbox=[1, 1, 8, 8])]
                if index in detection_indices
                else []
            ),
        )
        for index in range(count)
    ]


def test_keyframe_selector_returns_ordered_unique_keyframes():
    observations = make_observations(
        12, motion_indices={3, 4, 5}, detection_indices={4}
    )
    selected = select_keyframes(observations, max_keyframes=6)
    indices = [item.frame_index for item in selected]
    assert len(selected) <= 6
    assert indices == sorted(set(indices))
    assert 4 in indices


def test_keyframe_selector_includes_first_and_last_for_calm_video():
    selected = select_keyframes(make_observations(5), max_keyframes=4)
    indices = [item.frame_index for item in selected]
    assert indices[0] == 0
    assert indices[-1] == 4
