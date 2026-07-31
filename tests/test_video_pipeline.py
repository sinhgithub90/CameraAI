from __future__ import annotations

import numpy as np

from camera_ai.schemas import MotionResult, VideoFrameObservation


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
