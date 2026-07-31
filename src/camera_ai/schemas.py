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


class SecurityDecision(BaseModel):
    """Security conclusion derived from detections + VLM analysis."""

    alert_level: AlertLevel = AlertLevel.LOW
    risks: list[str] = Field(default_factory=list)
    recommended_action: str = ""


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
