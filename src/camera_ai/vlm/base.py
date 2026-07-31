from abc import ABC, abstractmethod

import numpy as np

from ..schemas import Detection, SceneAnalysis


class VLMAnalyzer(ABC):
    """Pluggable scene-analysis tier (Qwen-VL via Ollama, mock, ...)."""

    @abstractmethod
    def analyze(self, frames: list[np.ndarray], detections: list[Detection]) -> SceneAnalysis:
        """Analyse one or more frames (image: 1 frame; video: several sampled
        frames) given their detections; return a SceneAnalysis."""
        raise NotImplementedError
