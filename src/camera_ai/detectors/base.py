from abc import ABC, abstractmethod

import numpy as np

from ..schemas import Detection


class Detector(ABC):
    """Pluggable object-detection tier (YOLO, YOLO-World, motion gate, ...)."""

    @abstractmethod
    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Run detection on one BGR frame."""
        raise NotImplementedError
