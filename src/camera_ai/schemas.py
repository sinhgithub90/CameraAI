"""Data contracts for the core camera-AI module.

The core module is framework-agnostic: it never imports FastAPI. The FastAPI
adapter in apps/api translates HTTP requests into these objects and back.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class AlertLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class MediaType(str, Enum):
    IMAGE = "image"
    VIDEO = "video"


class EventObject(BaseModel):
    """One camera event submitted for analysis."""

    camera_id: str = "unknown"
    timestamp: datetime = Field(default_factory=datetime.now)
    image: str | bytes = Field(
        description="Local file path or raw file bytes (the adapter decides which)."
    )
    media_type: MediaType = MediaType.IMAGE
    metadata: dict[str, Any] = Field(default_factory=dict)


class Detection(BaseModel):
    """A single object detection from the detector tier."""

    label: str
    confidence: float = Field(ge=0.0, le=1.0)
    bbox: list[float] = Field(description="[x1, y1, x2, y2] pixel coordinates")
    track_id: int | None = None
    source: str = Field(
        default="yolo",
        description="Detector that produced this: 'yolo' | 'fire'.",
    )


class MotionRegion(BaseModel):
    """A rectangular image region that changed between video frames."""

    x: int
    y: int
    w: int
    h: int


class MotionResult(BaseModel):
    """Cheap frame-to-frame motion signal used before expensive detectors."""

    motion: bool = False
    changed_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    regions: list[MotionRegion] = Field(default_factory=list)
    score: float = Field(default=0.0, ge=0.0, le=1.0)


class VideoFrameObservation(BaseModel):
    """One sampled video frame and the analysis gathered for it."""

    frame_index: int = Field(ge=0)
    timestamp_seconds: float = Field(default=0.0, ge=0.0)
    motion: MotionResult = Field(default_factory=MotionResult)
    detections: list[Detection] = Field(default_factory=list)
    frame: Any = Field(default=None, exclude=True)


class VideoAnalysisStats(BaseModel):
    """Counters that make video sampling behavior observable."""

    frames_read: int = 0
    motion_frames: int = 0
    detector_frames: int = 0
    keyframes: int = 0
    windows_processed: int = 0
    windows_with_motion: int = 0
    vlm_calls: int = 0
    total_ms: float = 0.0
    motion_ms: float = 0.0
    detector_ms: float = 0.0
    qwen_ms: float = 0.0
    video_open_ms: float = 0.0
    frame_grab_ms: float = 0.0
    frame_retrieve_ms: float = 0.0
    frame_resize_ms: float = 0.0
    window_overhead_ms: float = 0.0
    untracked_ms: float = 0.0


class SceneAnalysis(BaseModel):
    """Raw scene-level output produced by the VLM tier."""

    summary: str = ""
    observations: list[str] = Field(default_factory=list)
    alert_level: AlertLevel = AlertLevel.LOW
    risks: list[str] = Field(default_factory=list)
    recommended_action: str = ""
    degraded: bool = Field(
        default=False,
        description="True when the VLM was unreachable and a fallback filled in.",
    )


class VLMResult(BaseModel):
    """VLM portion of the pipeline output."""

    summary: str
    observations: list[str] = Field(default_factory=list)
    degraded: bool = False
    skipped: bool = Field(
        default=False,
        description="True when the gate skipped the VLM (no cheap trigger fired).",
    )
    status: str = Field(
        default="completed",
        description="'pending' | 'completed' | 'skipped' — lifecycle state.",
    )


class SecurityDecision(BaseModel):
    """Security conclusion derived from detections + VLM analysis."""

    alert_level: AlertLevel = AlertLevel.LOW
    risks: list[str] = Field(default_factory=list)
    recommended_action: str = ""


class VideoWindowResult(BaseModel):
    """One five-second video window analyzed as a temporal event."""

    alert_id: str | None = Field(
        default=None,
        description="Async alert identifier for polling this window's VLM result.",
    )
    window_index: int
    start_seconds: float
    end_seconds: float
    detections: list[Detection] = Field(default_factory=list)
    vlm: VLMResult
    security: SecurityDecision
    keyframes: int = 0
    qwen_input: "QwenInputSummary"
    timing: "StageTiming"


class QwenInputSummary(BaseModel):
    """Safe-to-log description of the visual input sent to Qwen."""

    frame_indices: list[int] = Field(default_factory=list)
    timestamps_seconds: list[float] = Field(default_factory=list)
    frame_count: int = 0
    detection_labels: list[str] = Field(default_factory=list)


class StageTiming(BaseModel):
    """Processing duration in milliseconds for one video window."""

    total_ms: float = 0.0
    motion_ms: float = 0.0
    detector_ms: float = 0.0
    qwen_ms: float = 0.0


class PipelineResult(BaseModel):
    """Aggregated output of one analyze call."""

    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    media_type: MediaType
    camera_id: str = "unknown"
    detections: list[Detection] = Field(default_factory=list)
    vlm: VLMResult = Field(default_factory=VLMResult)
    security: SecurityDecision = Field(default_factory=SecurityDecision)
    annotated_image: str | None = Field(
        default=None,
        description="Base64 JPEG of the analysed frame with bounding boxes drawn.",
    )
    alert_ids: list[str] = Field(
        default_factory=list,
        description="Async alert identifiers, one per queued analysis window.",
    )
    video_stats: VideoAnalysisStats | None = None
    video_windows: list[VideoWindowResult] = Field(default_factory=list)
