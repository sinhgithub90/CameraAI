from __future__ import annotations

import logging

import numpy as np
import cv2

from camera_ai.schemas import MotionResult, VideoFrameObservation
from camera_ai.detectors.motion import MotionDetector
from camera_ai.video_selection import (
    _event_span,
    _smooth_activity_scores,
    select_keyframes,
)
from camera_ai.vlm.mock import MockAnalyzer
from camera_ai import SecurityAIPipeline
from camera_ai.schemas import EventObject, MediaType, SceneAnalysis, AlertLevel, Detection
from camera_ai.video_windows import RawVideoWindow


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


def make_scored_observations(scores: list[float]) -> list[VideoFrameObservation]:
    return [
        VideoFrameObservation(
            frame_index=index,
            timestamp_seconds=index / 5,
            motion=MotionResult(
                motion=score > 0,
                changed_ratio=score,
                score=score,
            ),
        )
        for index, score in enumerate(scores)
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


def test_activity_smoothing_uses_centered_three_sample_mean():
    assert _smooth_activity_scores([0.0, 3.0, 0.0]) == [1.5, 1.0, 1.5]


def test_activity_smoothing_preserves_empty_input():
    assert _smooth_activity_scores([]) == []


def test_event_span_bridges_one_inactive_sample():
    assert _event_span([1.0, 0.2, 1.0]) == (0, 2)


def test_event_span_stops_before_two_inactive_samples():
    assert _event_span([1.0, 0.2, 0.2, 1.0]) == (0, 0)


def test_event_span_uses_earliest_peak_on_tie():
    assert _event_span([0.2, 1.0, 0.2, 0.2, 1.0]) == (1, 1)


def test_two_keyframes_select_before_and_after_strongest_change():
    observations = make_observations(
        25,
        motion_indices={15},
        detection_indices={15},
    )

    selected = select_keyframes(observations, max_keyframes=2)

    assert [item.frame_index for item in selected] == [11, 20]


def test_two_keyframes_surround_complete_activity_span():
    observations = make_scored_observations(
        [0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
    )

    selected = select_keyframes(observations, max_keyframes=2)

    assert [item.frame_index for item in selected] == [1, 11]


def test_two_keyframes_use_ends_when_there_is_no_change():
    selected = select_keyframes(make_observations(5), max_keyframes=2)

    assert [item.frame_index for item in selected] == [0, 4]


def test_two_keyframes_select_after_frame_when_peak_is_first():
    observations = make_observations(
        8,
        motion_indices={0},
        detection_indices={0},
    )

    selected = select_keyframes(observations, max_keyframes=2)

    assert [item.frame_index for item in selected] == [0, 4]


def test_two_keyframes_select_before_frame_when_peak_is_last():
    observations = make_observations(
        21,
        motion_indices={20},
        detection_indices={20},
    )

    selected = select_keyframes(observations, max_keyframes=2)

    assert [item.frame_index for item in selected] == [16, 20]


def test_two_keyframes_return_single_observation_once():
    selected = select_keyframes(make_observations(1), max_keyframes=2)

    assert [item.frame_index for item in selected] == [0]


def test_two_keyframes_surround_detection_change_without_motion():
    observations = make_observations(20, detection_indices={12})

    selected = select_keyframes(observations, max_keyframes=2)

    assert [item.frame_index for item in selected] == [8, 17]


def test_two_keyframes_preserve_two_observations():
    selected = select_keyframes(make_observations(2), max_keyframes=2)

    assert [item.frame_index for item in selected] == [0, 1]


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


def test_video_resize_uses_fast_linear_interpolation(monkeypatch):
    frame = np.zeros((720, 1600, 3), dtype=np.uint8)
    capture = GrabOnlyCapture([frame, frame], fps=1.0)
    interpolation_modes: list[int] = []
    original_resize = cv2.resize

    def recording_resize(source, size, *, interpolation):
        interpolation_modes.append(interpolation)
        return original_resize(source, size, interpolation=interpolation)

    monkeypatch.setattr(
        "camera_ai.pipeline.cv2.VideoCapture",
        lambda source: capture,
    )
    monkeypatch.setattr("camera_ai.pipeline.cv2.resize", recording_resize)

    SecurityAIPipeline(
        detector=RecordingDetector(),
        vlm=RecordingVLM(),
        motion_fps=1.0,
        yolo_fps=1.0,
    ).analyze_event(EventObject(image="fake.mp4", media_type=MediaType.VIDEO))

    assert interpolation_modes == [cv2.INTER_LINEAR, cv2.INTER_LINEAR]


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


def test_video_stats_break_down_non_stage_overhead(tmp_path, caplog):
    calm = np.zeros((64, 64, 3), dtype=np.uint8)
    changed = calm.copy()
    changed[15:45, 20:50] = 255
    path = tmp_path / "profiled-motion.mp4"
    write_test_video(path, [calm, calm, changed, changed, calm], fps=1.0)

    with caplog.at_level(logging.INFO, logger="camera_ai.pipeline"):
        result = make_video_pipeline(
            RecordingDetector(),
            RecordingVLM(),
        ).analyze_event(
            EventObject(image=str(path), media_type=MediaType.VIDEO)
        )

    stats = result.video_stats
    assert stats is not None
    detailed_ms = (
        stats.video_open_ms
        + stats.frame_grab_ms
        + stats.frame_retrieve_ms
        + stats.frame_resize_ms
        + stats.motion_ms
        + stats.detector_ms
        + stats.qwen_ms
        + stats.window_overhead_ms
        + stats.untracked_ms
    )
    assert abs(stats.total_ms - detailed_ms) < 0.1
    assert stats.video_open_ms >= 0
    assert stats.frame_grab_ms >= 0
    assert stats.frame_retrieve_ms >= 0
    assert stats.frame_resize_ms >= 0
    assert stats.window_overhead_ms >= 0
    assert stats.untracked_ms >= 0
    assert "[video-overhead]" in caplog.text


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


def test_async_video_window_detection_preserves_keyframe_metadata(tmp_path):
    calm = np.zeros((64, 64, 3), dtype=np.uint8)
    changed = calm.copy()
    changed[15:45, 20:50] = 255
    path = tmp_path / "async-long-motion.mp4"
    write_test_video(path, [calm, changed, changed, calm, calm] * 2, fps=1.0)
    pipeline = SecurityAIPipeline(
        detector=RecordingDetector(),
        vlm=RecordingVLM(),
        motion_fps=1.0,
        yolo_fps=1.0,
        window_seconds=5.0,
        max_video_windows=None,
        max_keyframes=2,
    )

    windows = pipeline.detect_video_windows(
        EventObject(image=str(path), media_type=MediaType.VIDEO)
    )

    assert len(windows) == 2
    for window in windows:
        assert len(window["frames"]) <= 2
        assert len(window["frames"]) == len(window["frame_indices"])
        assert len(window["frames"]) == len(window["timestamps_seconds"])
        assert window["frame_indices"] == sorted(window["frame_indices"])


def test_async_video_windows_include_static_windows_and_stage_timing(tmp_path):
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    path = tmp_path / "async-static-windows.mp4"
    write_test_video(path, [frame] * 10, fps=1.0)
    pipeline = SecurityAIPipeline(
        detector=RecordingDetector(),
        vlm=RecordingVLM(),
        motion_fps=1.0,
        yolo_fps=1.0,
        window_seconds=5.0,
        max_keyframes=2,
    )

    windows = pipeline.detect_video_windows(
        EventObject(image=str(path), media_type=MediaType.VIDEO)
    )

    assert len(windows) == 2
    assert [window["window_index"] for window in windows] == [0, 1]
    assert all(len(window["frames"]) == 2 for window in windows)
    assert all(window["motion_ms"] >= 0 for window in windows)
    assert all(window["detector_ms"] >= 0 for window in windows)


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


def test_stream_window_runs_yolo_at_configured_rate(monkeypatch):
    class AlwaysMotion:
        def compare(self, frame):
            return MotionResult(motion=True, score=1.0)

    detector = RecordingDetector()
    pipeline = SecurityAIPipeline(detector=detector, vlm=RecordingVLM(), motion_fps=5.0, yolo_fps=2.0)
    monkeypatch.setattr("camera_ai.video_windows.MotionDetector", AlwaysMotion)
    observations = [
        VideoFrameObservation(frame_index=index * 6, timestamp_seconds=index / 5, frame=np.zeros((16, 16, 3), dtype=np.uint8))
        for index in range(5)
    ]
    pipeline.process_video_window(RawVideoWindow(window_index=0, start_seconds=0, observations=observations))
    assert detector.calls == 2
