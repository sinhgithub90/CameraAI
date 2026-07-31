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


def test_two_keyframes_prioritize_temporally_separated_event_frames():
    observations = make_observations(
        25,
        motion_indices={10, 15},
        detection_indices={10},
    )

    selected = select_keyframes(observations, max_keyframes=2)

    assert [item.frame_index for item in selected] == [10, 15]


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


class GrabOnlyCapture:
    def __init__(self, frames: list[np.ndarray], fps: float):
        self.frames = frames
        self.fps = fps
        self.position = 0
        self.current_index = -1
        self.grab_calls = 0
        self.retrieve_calls = 0

    def isOpened(self) -> bool:
        return True

    def get(self, prop: int) -> float:
        return self.fps if prop == cv2.CAP_PROP_FPS else 0.0

    def read(self):
        raise AssertionError("pipeline must not decode every frame with read()")

    def grab(self) -> bool:
        if self.position >= len(self.frames):
            return False
        self.current_index = self.position
        self.position += 1
        self.grab_calls += 1
        return True

    def retrieve(self):
        self.retrieve_calls += 1
        return True, self.frames[self.current_index].copy()

    def release(self) -> None:
        return None


def write_test_video(path, frames: list[np.ndarray], fps: float = 5.0) -> None:
    height, width = frames[0].shape[:2]
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )
    assert writer.isOpened()
    for frame in frames:
        writer.write(frame)
    writer.release()


def test_video_retrieves_only_motion_sample_frames(monkeypatch):
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    capture = GrabOnlyCapture([frame] * 12, fps=30.0)
    monkeypatch.setattr(
        "camera_ai.pipeline.cv2.VideoCapture",
        lambda source: capture,
    )

    result = SecurityAIPipeline(
        detector=RecordingDetector(),
        vlm=RecordingVLM(),
        motion_fps=5.0,
        yolo_fps=2.0,
    ).analyze_event(EventObject(image="fake.mp4", media_type=MediaType.VIDEO))

    assert result.video_stats is not None
    assert result.video_stats.frames_read == 12
    assert capture.grab_calls == 12
    assert capture.retrieve_calls == 2


def make_video_pipeline(detector, vlm):
    return SecurityAIPipeline(
        detector=detector,
        vlm=vlm,
        motion_fps=5.0,
        yolo_fps=2.0,
    )


def test_video_integration_static_skips_expensive_stages(tmp_path):
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    path = tmp_path / "static.mp4"
    write_test_video(path, [frame] * 8)
    detector = RecordingDetector()
    vlm = RecordingVLM()
    result = make_video_pipeline(detector, vlm).analyze_event(
        EventObject(image=str(path), media_type=MediaType.VIDEO)
    )
    assert detector.calls == 0
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
    vlm = RecordingVLM()
    result = make_video_pipeline(detector, vlm).analyze_event(
        EventObject(image=str(path), media_type=MediaType.VIDEO)
    )
    assert detector.calls < 8
    assert vlm.sequence_calls == 1
    assert vlm.sequence_lengths[0] == 2
    assert result.vlm.skipped is False
    assert result.video_stats is not None


def test_video_integration_processes_all_frames_in_five_second_windows(tmp_path):
    calm = np.zeros((64, 64, 3), dtype=np.uint8)
    changed = calm.copy()
    changed[15:45, 20:50] = 255
    frames = []
    for _ in range(3):
        frames.extend([calm, changed, changed, calm, calm])
    path = tmp_path / "long-motion.mp4"
    write_test_video(path, frames, fps=1.0)
    detector = RecordingDetector()
    vlm = RecordingVLM()
    pipeline = SecurityAIPipeline(
        detector=detector,
        vlm=vlm,
        motion_fps=1.0,
        yolo_fps=1.0,
        window_seconds=5.0,
        max_video_windows=None,
        max_keyframes=4,
    )
    result = pipeline.analyze_event(
        EventObject(image=str(path), media_type=MediaType.VIDEO)
    )
    assert result.video_stats is not None
    assert result.video_stats.frames_read == 15
    assert result.video_stats.windows_processed == 3
    assert result.video_stats.vlm_calls == 3
    assert len(result.video_windows) == 3
    assert vlm.sequence_calls == 3
    assert result.video_windows[0].qwen_input.frame_indices
    assert result.video_windows[0].keyframes <= 4
    assert result.video_windows[0].timing.qwen_ms >= 0


def test_video_integration_defaults_to_first_five_second_window(tmp_path):
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    path = tmp_path / "first-window-only.mp4"
    write_test_video(path, [frame] * 15, fps=1.0)
    vlm = RecordingVLM()
    result = SecurityAIPipeline(
        detector=RecordingDetector(),
        vlm=vlm,
        motion_fps=1.0,
        yolo_fps=1.0,
        window_seconds=5.0,
    ).analyze_event(EventObject(image=str(path), media_type=MediaType.VIDEO))
    assert result.video_stats is not None
    assert result.video_stats.frames_read == 5
    assert result.video_stats.windows_processed == 1
    assert vlm.sequence_calls == 0
