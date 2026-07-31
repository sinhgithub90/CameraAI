"""YOLO object detector backed by ultralytics.

The model is loaded lazily on first use and cached for the process lifetime:
importing this module (or building the pipeline) costs nothing until the first
real detection runs. This keeps RAM/VRAM usage down when the API is started
but no images have been analysed yet.
"""
from __future__ import annotations

import threading

import numpy as np
from ultralytics import YOLO
from ultralytics.utils.downloads import attempt_download_asset

from ..schemas import Detection
from .base import Detector

# Default COCO-pretrained weights. The "n" (nano) variant is small and fast,
# a good fit for the demo tier. Swap for yolo11s / yolo11m for more accuracy.
DEFAULT_WEIGHTS = "yolo11n.pt"
CONFIDENCE_THRESHOLD = 0.35


class YOLODetector(Detector):
    def __init__(
        self,
        weights: str = DEFAULT_WEIGHTS,
        conf: float = CONFIDENCE_THRESHOLD,
    ) -> None:
        self.weights = weights
        self.conf = conf
        self._model: YOLO | None = None
        self._lock = threading.Lock()

    def _ensure_model(self) -> YOLO:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    # Download the weights first if missing (first run only).
                    weights = attempt_download_asset(self.weights)
                    self._model = YOLO(weights)
        return self._model

    def detect(self, frame: np.ndarray) -> list[Detection]:
        model = self._ensure_model()
        results = model.predict(source=frame, conf=self.conf, verbose=False)
        detections: list[Detection] = []
        if not results or results[0].boxes is None:
            return detections
        names = model.names
        for box in results[0].boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            conf = float(box.conf[0])
            label = names[int(box.cls[0])]
            detections.append(
                Detection(label=label, confidence=conf, bbox=[x1, y1, x2, y2])
            )
        return detections
