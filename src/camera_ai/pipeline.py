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
from typing import TYPE_CHECKING, Callable

import cv2
import numpy as np

from .detectors.base import Detector
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
from .video_windows import (
    ProcessedVideoWindow,
    RawVideoWindow,
    StreamWindowProducer,
    VideoWindowProcessor,
)
from .vlm import OllamaQwenAnalyzer, VLMAnalyzer

if TYPE_CHECKING:
    from .queue import VLMTask

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
        max_keyframes: int = 2,
        window_seconds: float = 5.0,
        max_video_windows: int | None = 1,
        artifact_dir: str | None = None,
    ) -> None:
        self.detector = detector or YOLODetector()
        self.vlm = vlm or OllamaQwenAnalyzer()
        self.gate = gate or VLMGate()
        self.motion_detector = motion_detector or MotionDetector()
        self.motion_fps = motion_fps
        self.yolo_fps = yolo_fps
        self.max_keyframes = max_keyframes
        self.window_seconds = window_seconds
        self.max_video_windows = max_video_windows
        from .artifacts import WindowArtifactWriter
        artifact_writer = WindowArtifactWriter(artifact_dir) if artifact_dir else None
        self._stream_window_producer = StreamWindowProducer(
            motion_fps=motion_fps,
            window_seconds=window_seconds,
        )
        self._video_window_processor = VideoWindowProcessor(
            detector=self.detector,
            fire_detector=fire_detector,
            vlm=self.vlm,
            yolo_fps=yolo_fps,
            max_keyframes=max_keyframes,
            artifact_writer=artifact_writer,
        )

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
        if not self.gate.decide(detections):
            return self._build_skipped(event, MediaType.IMAGE, detections, frame)
        analysis = self.vlm.analyze(frame, detections)
        annotated = self._annotate(frame, detections)
        return self._build_result(event, MediaType.IMAGE, detections, analysis, annotated)

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
        total_video_open_ms = 0.0
        total_frame_grab_ms = 0.0
        total_frame_retrieve_ms = 0.0
        total_frame_resize_ms = 0.0
        total_window_overhead_ms = 0.0
        video_started = time.perf_counter()
        last_frame: np.ndarray | None = None
        try:
            video_open_started = time.perf_counter()
            cap = cv2.VideoCapture(source)
            if not cap.isOpened():
                raise ValueError("cannot open video input")
            source_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            motion_interval = max(1, round(source_fps / self.motion_fps))
            detector_interval = max(1, round(source_fps / self.yolo_fps))
            total_video_open_ms += (
                time.perf_counter() - video_open_started
            ) * 1000
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
                nonlocal total_window_overhead_ms
                flush_started = time.perf_counter()
                flush_qwen_ms = 0.0
                if not items:
                    return
                motion_frames += sum(1 for item in items if item.motion.motion)
                if not any(item.motion.motion for item in items):
                    current_motion_ms = 0.0
                    current_detector_ms = 0.0
                    total_window_overhead_ms += (
                        time.perf_counter() - flush_started
                    ) * 1000
                    return
                window_index = int(items[0].timestamp_seconds // self.window_seconds)
                keyframes = select_keyframes(items, max_keyframes=self.max_keyframes)
                frames = [item.frame for item in keyframes if item.frame is not None]
                detections = [d for item in items for d in item.detections]
                qwen_started = time.perf_counter()
                analysis = self.vlm.analyze_sequence(frames, detections)
                qwen_ms = (time.perf_counter() - qwen_started) * 1000
                flush_qwen_ms = qwen_ms
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
                total_window_overhead_ms += max(
                    0.0,
                    (time.perf_counter() - flush_started) * 1000 - flush_qwen_ms,
                )

            while True:
                if (
                    self.max_video_windows is not None
                    and idx >= source_fps * self.window_seconds * self.max_video_windows
                ):
                    break
                grab_started = time.perf_counter()
                grabbed = cap.grab()
                total_frame_grab_ms += (
                    time.perf_counter() - grab_started
                ) * 1000
                if not grabbed:
                    break
                frames_read += 1
                if idx % motion_interval == 0:
                    retrieve_started = time.perf_counter()
                    ok, frame = cap.retrieve()
                    total_frame_retrieve_ms += (
                        time.perf_counter() - retrieve_started
                    ) * 1000
                    if not ok:
                        break
                    last_frame = frame
                    resize_started = time.perf_counter()
                    frame = self._resize(
                        frame,
                        VIDEO_MAX_SIDE,
                        interpolation=cv2.INTER_LINEAR,
                    )
                    total_frame_resize_ms += (
                        time.perf_counter() - resize_started
                    ) * 1000
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
        total_ms = (time.perf_counter() - video_started) * 1000
        tracked_ms = (
            total_motion_ms
            + total_detector_ms
            + total_qwen_ms
            + total_video_open_ms
            + total_frame_grab_ms
            + total_frame_retrieve_ms
            + total_frame_resize_ms
            + total_window_overhead_ms
        )
        untracked_ms = max(0.0, total_ms - tracked_ms)
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
            total_ms=total_ms,
            motion_ms=total_motion_ms,
            detector_ms=total_detector_ms,
            qwen_ms=total_qwen_ms,
            video_open_ms=total_video_open_ms,
            frame_grab_ms=total_frame_grab_ms,
            frame_retrieve_ms=total_frame_retrieve_ms,
            frame_resize_ms=total_frame_resize_ms,
            window_overhead_ms=total_window_overhead_ms,
            untracked_ms=untracked_ms,
        )
        logger.info(
            "[video-overhead] open_ms=%.1f grab_ms=%.1f retrieve_ms=%.1f "
            "resize_ms=%.1f window_ms=%.1f untracked_ms=%.1f",
            stats.video_open_ms,
            stats.frame_grab_ms,
            stats.frame_retrieve_ms,
            stats.frame_resize_ms,
            stats.window_overhead_ms,
            stats.untracked_ms,
        )
        all_detections = [d for window in windows for d in window.detections]
        if not windows:
            return self._build_skipped(
                event, MediaType.VIDEO, all_detections, last_frame, stats
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

    # -- async pipeline methods -----------------------------------------

    def _read_video_frames(
        self,
        source: str,
    ) -> tuple[list[np.ndarray], list[Detection], float, np.ndarray | None]:
        """Đọc video, chạy motion+YOLO. Trả về (sampled_frames, all_detections, source_fps, last_frame).

        Creates a fresh MotionDetector per call so concurrent video requests
        never cross-contaminate each other's reference frames.
        """
        cap = cv2.VideoCapture(source)
        try:
            if not cap.isOpened():
                raise ValueError("cannot open video input")
            source_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            motion_interval = max(1, round(source_fps / self.motion_fps))
            detector_interval = max(1, round(source_fps / self.yolo_fps))
            motion_detector = MotionDetector()  # fresh instance — no race
            sampled_frames: list[np.ndarray] = []
            all_detections: list[Detection] = []
            last_frame: np.ndarray | None = None
            last_detector_index: int | None = None
            idx = 0
            while True:
                if (
                    self.max_video_windows is not None
                    and idx >= source_fps * self.window_seconds * self.max_video_windows
                ):
                    break
                grabbed = cap.grab()
                if not grabbed:
                    break
                if idx % motion_interval == 0:
                    ok, frame = cap.retrieve()
                    if not ok:
                        break
                    last_frame = frame
                    frame = self._resize(frame, VIDEO_MAX_SIDE, interpolation=cv2.INTER_LINEAR)
                    sampled_frames.append(frame)
                    motion = motion_detector.compare(frame)
                    if motion.motion and (
                        last_detector_index is None
                        or idx - last_detector_index >= detector_interval
                    ):
                        try:
                            all_detections.extend(self.detector.detect(frame))
                            last_detector_index = idx
                        except Exception:
                            logger.exception("video detector failed at frame %s", idx)
                idx += 1
        finally:
            cap.release()
        return sampled_frames, all_detections, source_fps, last_frame

    def detect(self, event: EventObject) -> PipelineResult:
        """Run detection + gate only. VLM runs later via queue. Returns immediately.

        For video: processes all 5-second windows without calling VLM.
        Use detect_video_windows() + enqueue per-window for full async.
        """
        if event.media_type == MediaType.IMAGE:
            return self._detect_image(event)
        return self._detect_video_flat(event)

    def _detect_image(self, event: EventObject) -> PipelineResult:
        frame = self._load_image(event.image)
        frame = self._resize(frame, IMAGE_MAX_SIDE)
        detections = self.detector.detect(frame)
        if not self.gate.decide(detections):
            return self._build_skipped(event, MediaType.IMAGE, detections, frame)
        return PipelineResult(
            media_type=MediaType.IMAGE,
            camera_id=event.camera_id,
            detections=detections,
            vlm=VLMResult(summary="", status="pending"),
            security=SecurityDecision(alert_level=AlertLevel.LOW),
            annotated_image=self._annotate(frame, detections),
        )

    def _detect_video_flat(self, event: EventObject) -> PipelineResult:
        """Quick video summary — one result for all windows (used by sync endpoint)."""
        source = event.image
        tmp_path: str | None = None
        if isinstance(source, bytes):
            tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            tmp.write(source)
            tmp.close()
            tmp_path = tmp.name
            source = tmp_path
        try:
            sampled, all_detections, _source_fps, last_frame = (
                self._read_video_frames(source)
            )
        finally:
            if tmp_path:
                os.unlink(tmp_path)

        if last_frame is None:
            raise ValueError("no readable frames in video")

        if not self.gate.decide(all_detections):
            return self._build_skipped(event, MediaType.VIDEO, all_detections, last_frame)

        return PipelineResult(
            media_type=MediaType.VIDEO,
            camera_id=event.camera_id,
            detections=all_detections,
            vlm=VLMResult(summary="", status="pending"),
            security=SecurityDecision(alert_level=AlertLevel.LOW),
            annotated_image=self._annotate(last_frame, all_detections),
        )

    def detect_video_windows(
        self,
        event: EventObject,
        max_windows: int | None = None,
        on_window: Callable[[dict], None] | None = None,
        collect: bool = True,
    ) -> list[dict]:
        """Read every video window and return per-window keyframes+detections.

        Each dict includes Motion/YOLO timing and is queued for VLM regardless
        of motion so the caller gets one VLM result for every time window.
        """
        source = event.image
        tmp_path: str | None = None
        if isinstance(source, bytes):
            tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            tmp.write(source)
            tmp.close()
            tmp_path = tmp.name
            source = tmp_path

        windows_out: list[dict] = []
        try:
            cap = cv2.VideoCapture(source)
            if not cap.isOpened():
                raise ValueError("cannot open video input")
            source_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            motion_interval = max(1, round(source_fps / self.motion_fps))
            detector_interval = max(1, round(source_fps / self.yolo_fps))
            motion_detector = MotionDetector()
            current_obs: list[VideoFrameObservation] = []
            current_window_idx: int | None = None
            last_detector_idx: int | None = None
            current_motion_ms = 0.0
            current_detector_ms = 0.0
            idx = 0

            def flush_window() -> None:
                nonlocal current_motion_ms, current_detector_ms
                if not current_obs or current_window_idx is None:
                    return
                keyframes = select_keyframes(
                    current_obs, max_keyframes=self.max_keyframes
                )
                frames = [o.frame for o in keyframes if o.frame is not None]
                dets = [d for o in current_obs for d in o.detections]
                window = {
                    "window_index": current_window_idx,
                    "start_seconds": current_window_idx * self.window_seconds,
                    "frames": frames,
                    "frame_indices": [o.frame_index for o in keyframes],
                    "timestamps_seconds": [
                        round(o.timestamp_seconds, 3) for o in keyframes
                    ],
                    "detections": dets,
                    "motion_ms": current_motion_ms,
                    "detector_ms": current_detector_ms,
                }
                if collect:
                    windows_out.append(window)
                if on_window is not None:
                    on_window(window)
                current_motion_ms = 0.0
                current_detector_ms = 0.0

            while True:
                if (
                    max_windows is not None
                    and current_window_idx is not None
                    and current_window_idx >= max_windows
                ):
                    break
                grabbed = cap.grab()
                if not grabbed:
                    break
                if idx % motion_interval == 0:
                    ok, frame = cap.retrieve()
                    if not ok:
                        break
                    frame = self._resize(frame, VIDEO_MAX_SIDE, interpolation=cv2.INTER_LINEAR)
                    window_idx = int((idx / source_fps) // self.window_seconds)
                    if current_window_idx is None:
                        current_window_idx = window_idx
                    elif window_idx != current_window_idx:
                        flush_window()
                        current_obs = []
                        current_window_idx = window_idx
                        last_detector_idx = None

                    motion_started = time.perf_counter()
                    motion = motion_detector.compare(frame)
                    current_motion_ms += (time.perf_counter() - motion_started) * 1000
                    obs = VideoFrameObservation(
                        frame_index=idx,
                        timestamp_seconds=idx / source_fps,
                        motion=motion,
                        frame=frame,
                    )
                    if motion.motion and (
                        last_detector_idx is None
                        or idx - last_detector_idx >= detector_interval
                    ):
                        try:
                            detector_started = time.perf_counter()
                            obs.detections = self.detector.detect(frame)
                            current_detector_ms += (
                                time.perf_counter() - detector_started
                            ) * 1000
                        except Exception:
                            logger.exception("video detector failed at frame %s", idx)
                        last_detector_idx = idx
                    current_obs.append(obs)
                idx += 1

            flush_window()
            cap.release()
        finally:
            if tmp_path:
                os.unlink(tmp_path)
        return windows_out

    def stream_video_windows(
        self,
        event: EventObject,
        on_window: Callable[[dict], None],
        max_windows: int | None = None,
    ) -> None:
        """Emit each completed 5-second window before reading the next one."""
        self.detect_video_windows(
            event, max_windows=max_windows, on_window=on_window, collect=False
        )

    def stream_video_chunks(self, event: EventObject, on_window: Callable[[RawVideoWindow], None]) -> None:
        """Compatibility facade for the queue-independent window producer."""
        self._stream_window_producer.stream(event, on_window)

    def process_video_window(
        self, window: RawVideoWindow, camera_id: str = "unknown"
    ) -> ProcessedVideoWindow:
        """Compatibility facade for the queue-independent window processor."""
        return self._video_window_processor.process(window, camera_id=camera_id)

    def analyze_vlm(self, task: VLMTask) -> SceneAnalysis:
        """Run VLM analysis on pre-detected frames. Called by VLMWorker (via asyncio.to_thread)."""
        if len(task.frames) == 0:
            return SceneAnalysis(
                summary="Không có frame để phân tích VLM.",
                alert_level=AlertLevel.MEDIUM if task.detections else AlertLevel.LOW,
                degraded=True,
            )
        if len(task.frames) == 1:
            return self.vlm.analyze(task.frames[0], task.detections)
        return self.vlm.analyze_sequence(task.frames, task.detections)

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
    def _resize(
        frame: np.ndarray,
        max_side: int,
        interpolation: int = cv2.INTER_AREA,
    ) -> np.ndarray:
        h, w = frame.shape[:2]
        if max(h, w) <= max_side:
            return frame
        scale = max_side / max(h, w)
        return cv2.resize(
            frame,
            (int(w * scale), int(h * scale)),
            interpolation=interpolation,
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
        frame: np.ndarray,
        video_stats: VideoAnalysisStats | None = None,
    ) -> PipelineResult:
        """Result when the gate skipped the VLM — no expensive analysis ran."""
        return PipelineResult(
            media_type=media_type,
            camera_id=event.camera_id,
            detections=detections,
            vlm=VLMResult(
                summary="Không có tín hiệu đáng chú ý — không kích hoạt phân tích VLM.",
                skipped=True,
                status="skipped",
            ),
            security=SecurityDecision(alert_level=AlertLevel.LOW),
            annotated_image=self._annotate(frame, detections),
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
