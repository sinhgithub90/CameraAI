"""Deterministic fallback VLM so the API can be exercised without Ollama.

Never used in production; exists so the pipeline and HTTP adapter can be
tested end-to-end even when no Ollama instance / model is available. Every
result it returns is marked degraded=True so callers can tell it apart.
"""
from __future__ import annotations

import numpy as np

from ..schemas import AlertLevel, Detection, SceneAnalysis
from .base import VLMAnalyzer


class MockAnalyzer(VLMAnalyzer):
    def analyze(self, frames: list[np.ndarray], detections: list[Detection]) -> SceneAnalysis:
        if not detections:
            return SceneAnalysis(
                summary="Cảnh bình thường, không phát hiện đối tượng đáng chú ý.",
                observations=["không có hoạt động đáng chú ý"],
                alert_level=AlertLevel.LOW,
                risks=[],
                recommended_action="khong_can_hanh_dong",
                degraded=True,
            )
        labels = sorted({d.label for d in detections})
        return SceneAnalysis(
            summary=f"Phát hiện: {', '.join(labels)}. (Mock — Ollama không khả dụng.)",
            observations=[f"phát hiện {d.label}" for d in detections],
            alert_level=AlertLevel.MEDIUM,
            risks=["phat_hien_nguoi" if "person" in labels else "phat_hien_doi_tuong"],
            recommended_action="xem_lai_clip",
            degraded=True,
        )
