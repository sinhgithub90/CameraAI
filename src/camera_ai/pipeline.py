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
    VLMResult,
)
from .video_selection import select_keyframes
from .vlm.base import VLMAnalyzer
from .vlm.ollama_qwen import OllamaQwenAnalyzer

logger = logging.getLogger(__name__)

IMAGE_MAX_SIDE = 1280
VIDEO_MAX_SIDE = 1280
VIDEO_MAX_FRAMES = 60       # cap for long clips


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
        max_keyframes: int = 8,
    ) -> None:
        self.detector = detector or YOLODetector()
        self.fire_detector = fire_detector or FireDetector()
        self.vlm = vlm or OllamaQwenAnalyzer()
        self.gate = gate or VLMGate()
        self.motion_detector = motion_detector or MotionDetector()
        self.motion_fps = motion_fps
        self.yolo_fps = yolo_fps
        self.max_keyframes = max_keyframes

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
        try:
            cap = cv2.VideoCapture(source)
            if not cap.isOpened():
                raise ValueError("cannot open video input")
            source_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            motion_interval = max(1, round(source_fps / self.motion_fps))
            detector_interval = max(1, round(source_fps / self.yolo_fps))
            self.motion_detector.reset()
            observations: list[VideoFrameObservation] = []
            last_detector_index: int | None = None
            frames_read = 0
            motion_frames = 0
            detector_frames = 0
            idx = 0
            while frames_read < VIDEO_MAX_FRAMES:
                ok, frame = cap.read()
                if not ok:
                    break
                frames_read += 1
                if idx % motion_interval == 0:
                    frame = self._resize(frame, VIDEO_MAX_SIDE)
                    motion = self.motion_detector.compare(frame)
                    observation = VideoFrameObservation(
                        frame_index=idx,
                        timestamp_seconds=idx / source_fps,
                        motion=motion,
                        frame=frame,
                    )
                    if motion.motion:
                        motion_frames += 1
                        if (
                            last_detector_index is None
                            or idx - last_detector_index >= detector_interval
                        ):
                            try:
                                detections = self.detector.detect(frame)
                                detections += self.fire_detector.detect(frame)
                                observation.detections = detections
                                detector_frames += 1
                                last_detector_index = idx
                            except Exception:  # noqa: BLE001 - preserve VLM path
                                logger.exception("video detector failed at frame %s", idx)
                    observations.append(observation)
                idx += 1
            cap.release()
        finally:
            if tmp_path:
                os.unlink(tmp_path)

        stats = VideoAnalysisStats(
            frames_read=frames_read,
            motion_frames=motion_frames,
            detector_frames=detector_frames,
        )
        if not observations:
            raise ValueError("no readable frames in video")

        all_detections = [d for item in observations for d in item.detections]
        if not any(item.motion.motion for item in observations):
            return self._build_skipped(
                event,
                MediaType.VIDEO,
                all_detections,
                [],
                observations[-1].frame,
                stats,
            )

        keyframes = select_keyframes(observations, max_keyframes=self.max_keyframes)
        frames = [item.frame for item in keyframes if item.frame is not None]
        stats.keyframes = len(frames)
        analysis = self.vlm.analyze_sequence(frames, all_detections)
        representative = max(
            keyframes,
            key=lambda item: item.motion.score
            + max((d.confidence for d in item.detections), default=0.0),
        )
        annotated = self._annotate(representative.frame, representative.detections)
        return self._build_result(
            event,
            MediaType.VIDEO,
            all_detections,
            analysis,
            annotated,
            stats,
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
        )
