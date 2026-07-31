from __future__ import annotations

import numpy as np
import cv2

from camera_ai.schemas import MotionResult, VideoFrameObservation
from camera_ai.detectors.motion import MotionDetector
from camera_ai.video_selection import select_keyframes
from camera_ai.vlm.mock import MockAnalyzer
from camera_ai import SecurityAIPipeline
from camera_ai.schemas import EventObject, MediaType, SceneAnalysis, AlertLevel, Detection


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


def test_vlm_sequence_analysis_is_called_once():
    vlm = MockAnalyzer()
    frame = np.zeros((16, 16, 3), dtype=np.uint8)
    result = vlm.analyze_sequence([frame, frame.copy()], [])
    assert vlm.sequence_calls == 1
    assert result.degraded is True


class RecordingDetector:
    def __init__(self, detections: list[Detection] | None = None):
        self.calls = 0
        self.detections = detections or []

    def detect(self, frame: np.ndarray) -> list[Detection]:
        self.calls += 1
        return list(self.detections)


class RecordingVLM:
    def __init__(self):
        self.sequence_calls = 0
        self.sequence_lengths: list[int] = []

    def analyze(self, frame: np.ndarray, detections: list[Detection]) -> SceneAnalysis:
        return SceneAnalysis(summary="single", alert_level=AlertLevel.LOW)

    def analyze_sequence(
        self, frames: list[np.ndarray], detections: list[Detection]
    ) -> SceneAnalysis:
        self.sequence_calls += 1
        self.sequence_lengths.append(len(frames))
        return SceneAnalysis(summary="sequence", alert_level=AlertLevel.MEDIUM)


def write_test_video(path, frames: list[np.ndarray], fps: float = 5.0) -> None:
    height, width = frames[0].shape[:2]
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )
    assert writer.isOpened()
    for frame in frames:
        writer.write(frame)
    writer.release()


def make_video_pipeline(detector, fire_detector, vlm):
    return SecurityAIPipeline(
        detector=detector,
        fire_detector=fire_detector,
        vlm=vlm,
        motion_fps=5.0,
        yolo_fps=2.0,
        max_keyframes=8,
    )


def test_video_integration_static_skips_expensive_stages(tmp_path):
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    path = tmp_path / "static.mp4"
    write_test_video(path, [frame] * 8)
    detector = RecordingDetector()
    fire_detector = RecordingDetector()
    vlm = RecordingVLM()
    result = make_video_pipeline(detector, fire_detector, vlm).analyze_event(
        EventObject(image=str(path), media_type=MediaType.VIDEO)
    )
    assert detector.calls == 0
    assert fire_detector.calls == 0
    assert vlm.sequence_calls == 0
    assert result.vlm.skipped is True


def test_video_integration_motion_calls_vlm_once_with_bounded_keyframes(tmp_path):
    calm = np.zeros((64, 64, 3), dtype=np.uint8)
    changed = calm.copy()
    changed[15:45, 20:50] = 255
    path = tmp_path / "motion.mp4"
    write_test_video(path, [calm, calm, calm, changed, changed, changed, calm, calm])
    detector = RecordingDetector(
        [Detection(label="person", confidence=0.9, bbox=[15, 15, 50, 45])]
    )
    fire_detector = RecordingDetector()
    vlm = RecordingVLM()
    result = make_video_pipeline(detector, fire_detector, vlm).analyze_event(
        EventObject(image=str(path), media_type=MediaType.VIDEO)
    )
    assert detector.calls < 8
    assert fire_detector.calls < 8
    assert vlm.sequence_calls == 1
    assert vlm.sequence_lengths[0] <= 8
    assert result.vlm.skipped is False
    assert result.video_stats is not None
