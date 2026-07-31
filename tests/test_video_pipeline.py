from __future__ import annotations

import numpy as np

from camera_ai.schemas import MotionResult, VideoFrameObservation
from camera_ai.detectors.motion import MotionDetector


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
