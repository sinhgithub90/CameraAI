from abc import ABC, abstractmethod
from collections.abc import Sequence

import numpy as np

from ..schemas import Detection, SceneAnalysis


class VLMAnalyzer(ABC):
    """Pluggable scene-analysis tier (Qwen-VL via Ollama, mock, ...)."""

    @abstractmethod
    def analyze(self, frames: list[np.ndarray], detections: list[Detection]) -> SceneAnalysis:
        """Analyse one or more frames (image: 1 frame; video: several sampled
        frames) given their detections; return a SceneAnalysis."""
        raise NotImplementedError

    def analyze_sequence(
        self,
        frames: Sequence[np.ndarray],
        detections: list[Detection],
    ) -> SceneAnalysis:
        """Analyse a temporal sequence, preserving compatibility with old VLMs."""
        if not frames:
            raise ValueError("at least one frame is required")
        return self.analyze(frames[len(frames) // 2], detections)
