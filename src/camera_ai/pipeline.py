"""SecurityAIPipeline — entry point of the core camera-AI module.

Runs a detector tier (object detection) followed by a VLM tier (scene-level
security analysis) and turns a single EventObject into a structured
PipelineResult.

This module never imports FastAPI, so the same pipeline can later be driven
from a queue worker without any changes to core logic.
"""
from __future__ import annotations

import base64
import logging
import os
import tempfile
import time

import cv2
import numpy as np

from .detectors.base import Detector
from .detectors.fire import FireDetector
from .detectors.motion import MotionDetector
from .detectors.yolo import YOLODetector
from .gate import VLMGate
from .schemas import (
    AlertLevel,
    Detection,
    EventObject,
    MediaType,
    PipelineResult,
    SceneAnalysis,
    SecurityDecision,
    VideoAnalysisStats,
    VideoFrameObservation,
    QwenInputSummary,
    StageTiming,
    VideoWindowResult,
    VLMResult,
)
from .video_selection import select_keyframes
from .vlm.base import VLMAnalyzer
from .vlm.ollama_qwen import OllamaQwenAnalyzer

logger = logging.getLogger(__name__)

IMAGE_MAX_SIDE = 1280
VIDEO_MAX_SIDE = 1280


class SecurityAIPipeline:
    def __init__(
        self,
        detector: Detector | None = None,
        fire_detector: Detector | None = None,
        vlm: VLMAnalyzer | None = None,
        gate: VLMGate | None = None,
        motion_detector: MotionDetector | None = None,
        motion_fps: float = 5.0,
        yolo_fps: float = 2.0,
        max_keyframes: int = 4,
        window_seconds: float = 5.0,
        max_video_windows: int | None = 1,
    ) -> None:
        self.detector = detector or YOLODetector()
        self.fire_detector = fire_detector or FireDetector()
        self.vlm = vlm or OllamaQwenAnalyzer()
        self.gate = gate or VLMGate()
        self.motion_detector = motion_detector or MotionDetector()
        self.motion_fps = motion_fps
        self.yolo_fps = yolo_fps
        self.max_keyframes = max_keyframes
        self.window_seconds = window_seconds
        self.max_video_windows = max_video_windows

    # -- public API -------------------------------------------------------

    def analyze_event(self, event: EventObject) -> PipelineResult:
        if event.media_type == MediaType.VIDEO:
            return self._analyze_video(event)
        return self._analyze_image(event)

    # -- image ------------------------------------------------------------

    def _analyze_image(self, event: EventObject) -> PipelineResult:
        frame = self._load_image(event.image)
        frame = self._resize(frame, IMAGE_MAX_SIDE)
        detections = self.detector.detect(frame)
        fire_detections = self.fire_detector.detect(frame)
        if not self.gate.decide(detections, fire_detections):
            return self._build_skipped(
                event, MediaType.IMAGE, detections, fire_detections, frame
            )
        all_detections = detections + fire_detections
        analysis = self.vlm.analyze(frame, all_detections)
        annotated = self._annotate(frame, all_detections)
        return self._build_result(event, MediaType.IMAGE, all_detections, analysis, annotated)

    # -- video ------------------------------------------------------------

    def _analyze_video(self, event: EventObject) -> PipelineResult:
        source = event.image
        tmp_path: str | None = None
        if isinstance(source, bytes):
            tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            tmp.write(source)
            tmp.close()
            tmp_path = tmp.name
            source = tmp_path

        windows: list[VideoWindowResult] = []
        representatives: list[tuple[VideoWindowResult, np.ndarray, list[Detection]]] = []
        frames_read = 0
        motion_frames = 0
        detector_frames = 0
        total_motion_ms = 0.0
        total_detector_ms = 0.0
        total_qwen_ms = 0.0
        video_started = time.perf_counter()
        last_frame: np.ndarray | None = None
        try:
            cap = cv2.VideoCapture(source)
            if not cap.isOpened():
                raise ValueError("cannot open video input")
            source_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            motion_interval = max(1, round(source_fps / self.motion_fps))
            detector_interval = max(1, round(source_fps / self.yolo_fps))
            self.motion_detector.reset()
            current_window: list[VideoFrameObservation] = []
            current_window_index: int | None = None
            last_detector_index: int | None = None
            current_motion_ms = 0.0
            current_detector_ms = 0.0
            idx = 0

            def flush_window(items: list[VideoFrameObservation]) -> None:
                nonlocal motion_frames, detector_frames, current_motion_ms
                nonlocal current_detector_ms, total_qwen_ms
                if not items:
                    return
                motion_frames += sum(1 for item in items if item.motion.motion)
                if not any(item.motion.motion for item in items):
                    current_motion_ms = 0.0
                    current_detector_ms = 0.0
                    return
                window_index = int(items[0].timestamp_seconds // self.window_seconds)
                keyframes = select_keyframes(items, max_keyframes=self.max_keyframes)
                frames = [item.frame for item in keyframes if item.frame is not None]
                detections = [d for item in items for d in item.detections]
                qwen_started = time.perf_counter()
                analysis = self.vlm.analyze_sequence(frames, detections)
                qwen_ms = (time.perf_counter() - qwen_started) * 1000
                total_qwen_ms += qwen_ms
                representative = max(
                    keyframes,
                    key=lambda item: item.motion.score
                    + max((d.confidence for d in item.detections), default=0.0),
                )
                window_result = VideoWindowResult(
                    window_index=window_index,
                    start_seconds=window_index * self.window_seconds,
                    end_seconds=(window_index + 1) * self.window_seconds,
                    detections=detections,
                    vlm=VLMResult(
                        summary=analysis.summary,
                        observations=analysis.observations,
                        degraded=analysis.degraded,
                    ),
                    security=SecurityDecision(
                        alert_level=analysis.alert_level,
                        risks=analysis.risks,
                        recommended_action=analysis.recommended_action,
                    ),
                    keyframes=len(frames),
                    qwen_input=QwenInputSummary(
                        frame_indices=[item.frame_index for item in keyframes],
                        timestamps_seconds=[
                            round(item.timestamp_seconds, 3) for item in keyframes
                        ],
                        frame_count=len(frames),
                        detection_labels=sorted({d.label for d in detections}),
                    ),
                    timing=StageTiming(
                        total_ms=current_motion_ms + current_detector_ms + qwen_ms,
                        motion_ms=current_motion_ms,
                        detector_ms=current_detector_ms,
                        qwen_ms=qwen_ms,
                    ),
                )
                windows.append(window_result)
                representatives.append(
                    (window_result, representative.frame, representative.detections)
                )
                logger.info(
                    "[video] window=%s qwen_input_frames=%s labels=%s "
                    "keyframes=%s motion_ms=%.1f detector_ms=%.1f qwen_ms=%.1f "
                    "summary=%s",
                    window_result.window_index,
                    window_result.qwen_input.frame_indices,
                    window_result.qwen_input.detection_labels,
                    window_result.keyframes,
                    window_result.timing.motion_ms,
                    window_result.timing.detector_ms,
                    window_result.timing.qwen_ms,
                    analysis.summary[:200],
                )
                current_motion_ms = 0.0
                current_detector_ms = 0.0

            while True:
                if (
                    self.max_video_windows is not None
                    and idx >= source_fps * self.window_seconds * self.max_video_windows
                ):
                    break
                ok, frame = cap.read()
                if not ok:
                    break
                frames_read += 1
                last_frame = frame
                if idx % motion_interval == 0:
                    frame = self._resize(frame, VIDEO_MAX_SIDE)
                    window_index = int((idx / source_fps) // self.window_seconds)
                    if current_window_index is None:
                        current_window_index = window_index
                    elif window_index != current_window_index:
                        flush_window(current_window)
                        current_window = []
                        current_window_index = window_index
                    motion_started = time.perf_counter()
                    motion = self.motion_detector.compare(frame)
                    motion_ms = (time.perf_counter() - motion_started) * 1000
                    total_motion_ms += motion_ms
                    current_motion_ms += motion_ms
                    observation = VideoFrameObservation(
                        frame_index=idx,
                        timestamp_seconds=idx / source_fps,
                        motion=motion,
                        frame=frame,
                    )
                    if motion.motion and (
                        last_detector_index is None
                        or idx - last_detector_index >= detector_interval
                    ):
                        try:
                            detector_started = time.perf_counter()
                            detections = self.detector.detect(frame)
                            detections += self.fire_detector.detect(frame)
                            observation.detections = detections
                            detector_frames += 1
                            detector_ms = (time.perf_counter() - detector_started) * 1000
                            total_detector_ms += detector_ms
                            current_detector_ms += detector_ms
                            last_detector_index = idx
                        except Exception:  # noqa: BLE001 - preserve VLM path
                            logger.exception("video detector failed at frame %s", idx)
                    current_window.append(observation)
                idx += 1
            flush_window(current_window)
            cap.release()
        finally:
            if tmp_path:
                os.unlink(tmp_path)

        if not frames_read or last_frame is None:
            raise ValueError("no readable frames in video")
        stats = VideoAnalysisStats(
            frames_read=frames_read,
            motion_frames=motion_frames,
            detector_frames=detector_frames,
            keyframes=sum(window.keyframes for window in windows),
            windows_processed=max(
                1, int((frames_read - 1) / (source_fps * self.window_seconds)) + 1
            ),
            windows_with_motion=len(windows),
            vlm_calls=len(windows),
            total_ms=(time.perf_counter() - video_started) * 1000,
            motion_ms=total_motion_ms,
            detector_ms=total_detector_ms,
            qwen_ms=total_qwen_ms,
        )
        all_detections = [d for window in windows for d in window.detections]
        if not windows:
            return self._build_skipped(
                event, MediaType.VIDEO, all_detections, [], last_frame, stats
            )

        alert_rank = {AlertLevel.LOW: 0, AlertLevel.MEDIUM: 1, AlertLevel.HIGH: 2}
        best_window, representative_frame, representative_detections = max(
            representatives,
            key=lambda item: alert_rank[item[0].security.alert_level],
        )
        analysis = SceneAnalysis(
            summary=best_window.vlm.summary,
            observations=best_window.vlm.observations,
            alert_level=best_window.security.alert_level,
            risks=best_window.security.risks,
            recommended_action=best_window.security.recommended_action,
            degraded=best_window.vlm.degraded,
        )
        return self._build_result(
            event,
            MediaType.VIDEO,
            all_detections,
            analysis,
            self._annotate(representative_frame, representative_detections),
            stats,
            windows,
        )

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _load_image(image: str | bytes) -> np.ndarray:
        if isinstance(image, str):
            frame = cv2.imread(image)
            if frame is None:
                raise ValueError(f"cannot read image path: {image}")
            return frame
        data = np.frombuffer(image, dtype=np.uint8)
        frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("cannot decode image bytes")
        return frame

    @staticmethod
    def _resize(frame: np.ndarray, max_side: int) -> np.ndarray:
        h, w = frame.shape[:2]
        if max(h, w) <= max_side:
            return frame
        scale = max_side / max(h, w)
        return cv2.resize(
            frame,
            (int(w * scale), int(h * scale)),
            interpolation=cv2.INTER_AREA,
        )

    @staticmethod
    def _annotate(frame: np.ndarray, detections: list[Detection]) -> str | None:
        """Draw bboxes + labels on a copy of the frame; return base64 JPEG.

        Always returns an image, even with zero detections, so the UI has
        something to show for high-alert scenes where YOLO finds nothing
        (fire, smoke, explosion, ...). Fire detections are drawn orange,
        object detections green.
        """
        annotated = frame.copy()
        for d in detections:
            x1, y1, x2, y2 = (int(round(v)) for v in d.bbox)
            color = (0, 165, 255) if d.source == "fire" else (0, 200, 0)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            label = f"{d.label} {d.confidence:.2f}"
            cv2.putText(
                annotated,
                label,
                (x1, max(y1 - 8, 12)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
            )
        ok, buf = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        if not ok:
            return None
        return base64.b64encode(buf.tobytes()).decode("ascii")

    def _build_skipped(
        self,
        event: EventObject,
        media_type: MediaType,
        detections: list[Detection],
        fire_detections: list[Detection],
        frame: np.ndarray,
        video_stats: VideoAnalysisStats | None = None,
    ) -> PipelineResult:
        """Result when the gate skipped the VLM — no expensive analysis ran."""
        all_detections = detections + fire_detections
        return PipelineResult(
            media_type=media_type,
            camera_id=event.camera_id,
            detections=all_detections,
            vlm=VLMResult(
                summary="Không có tín hiệu đáng chú ý — không kích hoạt phân tích VLM.",
                skipped=True,
            ),
            security=SecurityDecision(alert_level=AlertLevel.LOW),
            annotated_image=self._annotate(frame, all_detections),
            video_stats=video_stats,
        )

    @staticmethod
    def _build_result(
        event: EventObject,
        media_type: MediaType,
        detections: list[Detection],
        analysis: SceneAnalysis,
        annotated: str | None = None,
        video_stats: VideoAnalysisStats | None = None,
        video_windows: list[VideoWindowResult] | None = None,
    ) -> PipelineResult:
        return PipelineResult(
            media_type=media_type,
            camera_id=event.camera_id,
            detections=detections,
            vlm=VLMResult(
                summary=analysis.summary,
                observations=analysis.observations,
                degraded=analysis.degraded,
            ),
            security=SecurityDecision(
                alert_level=analysis.alert_level,
                risks=analysis.risks,
                recommended_action=analysis.recommended_action,
            ),
            annotated_image=annotated,
            video_stats=video_stats,
            video_windows=video_windows or [],
        )
