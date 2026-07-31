"""Camera-AI core module — framework-agnostic security-camera analysis.

Usage:
    from camera_ai import SecurityAIPipeline, EventObject
    result = SecurityAIPipeline().analyze_event(
        EventObject(camera_id="cam_01", image="/path/to/frame.jpg")
    )
"""
from .detectors.fire import FireDetector
from .gate import VLMGate
from .pipeline import SecurityAIPipeline
from .schemas import (
    AlertLevel,
    Detection,
    EventObject,
    MediaType,
    PipelineResult,
    SceneAnalysis,
    SecurityDecision,
    VLMResult,
)

__version__ = "0.1.0"

__all__ = [
    "SecurityAIPipeline",
    "VLMGate",
    "FireDetector",
    "AlertLevel",
    "Detection",
    "EventObject",
    "MediaType",
    "PipelineResult",
    "SceneAnalysis",
    "SecurityDecision",
    "VLMResult",
]
