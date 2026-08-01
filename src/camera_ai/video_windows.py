"""Self-contained contracts and processing for five-second video windows.

This module owns decoding/sampling a source into windows and running the
per-window Motion -> detector -> VLM workflow.  It deliberately has no
dependency on the application queue, HTTP layer, or analysis persistence.
"""
from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Callable

import cv2
import numpy as np
from pydantic import BaseModel, Field

from .detectors.base import Detector
from .detectors.motion import MotionDetector
from .schemas import (
    AlertLevel,
    Detection,
    EventObject,
    QwenInputSummary,
    SceneAnalysis,
    StageTiming,
    VideoFrameObservation,
)
from .video_selection import select_keyframes
from .vlm import VLMAnalyzer

VIDEO_MAX_SIDE = 1280


class RawVideoWindow(BaseModel):
    window_index: int
    start_seconds: float
    window_seconds: float = 5.0
    observations: list[VideoFrameObservation] = Field(default_factory=list)

    @property
    def end_seconds(self) -> float:
        return self.start_seconds + self.window_seconds


class ProcessedVideoWindow(BaseModel):
    scene: SceneAnalysis
    detections: list[Detection] = Field(default_factory=list)
    qwen_input: QwenInputSummary
    timing: StageTiming


class StreamWindowProducer:
    """Decode a source once and emit independently processable time windows."""

    def __init__(
        self,
        *,
        motion_fps: float,
        window_seconds: float,
        max_side: int = VIDEO_MAX_SIDE,
    ) -> None:
        self.motion_fps = motion_fps
        self.window_seconds = window_seconds
        self.max_side = max_side

    def stream(self, event: EventObject, on_window: Callable[[RawVideoWindow], None]) -> None:
        """Emit the first segment immediately and pace later source-time segments."""
        source = event.image
        tmp_path: str | None = None
        if isinstance(source, bytes):
            tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            try:
                tmp.write(source)
            finally:
                tmp.close()
            tmp_path = tmp.name
            source = tmp_path
        cap: cv2.VideoCapture | None = None
        try:
            cap = cv2.VideoCapture(source)
            if not cap.isOpened():
                raise ValueError("cannot open video input")
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            interval = max(1, round(fps / self.motion_fps))
            started = time.monotonic()
            idx = 0
            current_idx = 0
            observations: list[VideoFrameObservation] = []

            def flush() -> None:
                if observations:
                    on_window(
                        RawVideoWindow(
                            window_index=current_idx,
                            start_seconds=current_idx * self.window_seconds,
                            window_seconds=self.window_seconds,
                            observations=list(observations),
                        )
                    )

            while cap.grab():
                if idx % interval == 0:
                    timestamp = idx / fps
                    ok, frame = cap.retrieve()
                    if not ok:
                        break
                    window_idx = int(timestamp // self.window_seconds)
                    if window_idx != current_idx:
                        flush()
                        delay = started + window_idx * self.window_seconds - time.monotonic()
                        if delay > 0:
                            time.sleep(delay)
                        observations.clear()
                        current_idx = window_idx
                    observations.append(
                        VideoFrameObservation(
                            frame_index=idx,
                            timestamp_seconds=timestamp,
                            frame=self._resize(frame),
                        )
                    )
                idx += 1
            flush()
        finally:
            if cap is not None:
                cap.release()
            if tmp_path:
                os.unlink(tmp_path)

    def _resize(self, frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        if max(height, width) <= self.max_side:
            return frame
        scale = self.max_side / max(height, width)
        return cv2.resize(
            frame,
            (int(width * scale), int(height * scale)),
            interpolation=cv2.INTER_LINEAR,
        )


class VideoWindowProcessor:
    """Run all inference for one closed raw window without queue/store state."""

    def __init__(
        self,
        *,
        detector: Detector,
        vlm: VLMAnalyzer,
        yolo_fps: float,
        max_keyframes: int,
    ) -> None:
        self.detector = detector
        self.vlm = vlm
        self.yolo_fps = yolo_fps
        self.max_keyframes = max_keyframes

    def process(self, window: RawVideoWindow) -> ProcessedVideoWindow:
        motion_detector = MotionDetector()
        detections: list[Detection] = []
        motion_ms = detector_ms = 0.0
        last_detector_seconds = float("-inf")
        for observation in window.observations:
            started = time.perf_counter()
            observation.motion = motion_detector.compare(observation.frame)
            motion_ms += (time.perf_counter() - started) * 1000
            if (
                observation.motion.motion
                and observation.timestamp_seconds - last_detector_seconds >= 1 / self.yolo_fps
            ):
                started = time.perf_counter()
                observation.detections = self.detector.detect(observation.frame)
                detector_ms += (time.perf_counter() - started) * 1000
                last_detector_seconds = observation.timestamp_seconds
            detections.extend(observation.detections)

        keyframes = select_keyframes(window.observations, max_keyframes=self.max_keyframes)
        frames = [item.frame for item in keyframes if item.frame is not None]
        started = time.perf_counter()
        if not frames:
            scene = SceneAnalysis(
                summary="Không có frame để phân tích VLM.",
                alert_level=AlertLevel.MEDIUM if detections else AlertLevel.LOW,
                degraded=True,
            )
        elif len(frames) == 1:
            scene = self.vlm.analyze(frames[0], detections)
        else:
            scene = self.vlm.analyze_sequence(frames, detections)
        qwen_ms = (time.perf_counter() - started) * 1000
        return ProcessedVideoWindow(
            scene=scene,
            detections=detections,
            qwen_input=QwenInputSummary(
                frame_indices=[item.frame_index for item in keyframes],
                timestamps_seconds=[round(item.timestamp_seconds, 3) for item in keyframes],
                frame_count=len(frames),
                detection_labels=sorted({item.label for item in detections}),
            ),
            timing=StageTiming(
                motion_ms=motion_ms,
                detector_ms=detector_ms,
                qwen_ms=qwen_ms,
                total_ms=motion_ms + detector_ms + qwen_ms,
            ),
        )
