from abc import ABC, abstractmethod

import numpy as np

from ..schemas import Detection, SceneAnalysis


class VLMAnalyzer(ABC):
    """Pluggable scene-analysis tier (Qwen-VL via Ollama, mock, ...)."""

    @abstractmethod
    def analyze(self, frame: np.ndarray, detections: list[Detection]) -> SceneAnalysis:
        """Analyse a frame given its detections; return a SceneAnalysis."""
        raise NotImplementedError
