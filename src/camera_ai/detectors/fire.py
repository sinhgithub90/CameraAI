"""Fire/smoke trigger tier.

The fire tier feeds the cheap trigger layer that gates the VLM. It has two
backends behind the same Detector interface:

  - a real YOLO model trained on fire/smoke, when FIRE_MODEL is set (a local
    .pt path or a URL — downloaded on first use). The default points at a
    light YOLO11n fire/smoke model (~5MB, mAP50 ~0.79).
  - otherwise a tiny colour heuristic (orange/yellow "hot" pixels) so the demo
    still catches flame scenes with zero downloads. Less precise than the
    model, but cheap and always available.

Set FIRE_MODEL=none|off to disable the fire tier entirely.
"""
from __future__ import annotations

import logging
import os
import threading
import urllib.parse

import cv2
import numpy as np
from ultralytics import YOLO
from ultralytics.utils.downloads import attempt_download_asset, safe_download

from ..schemas import Detection
from .base import Detector

logger = logging.getLogger(__name__)

FIRE_MODEL_ENV = "FIRE_MODEL"
# Light pretrained YOLO11n fire/smoke weights (mAP50 ~0.79), from HuggingFace.
DEFAULT_FIRE_URL = (
    "https://huggingface.co/LK-ROBOTICS/nxp-sf-yolo11n-balanced/resolve/main/"
    "revisions/v1/best.pt"
)
# Keep this a bit above 0.3: the light fire model throws false positives
# (e.g. red/yellow objects) below ~0.4.
CONFIDENCE_THRESHOLD = 0.40

# Flame-colour cue (HSV): orange/yellow, saturated, bright.
_FIRE_HSV_LOW = np.array([5, 90, 140])
_FIRE_HSV_HIGH = np.array([35, 255, 255])


class FireDetector(Detector):
    def __init__(self, model: str = "auto", conf: float = CONFIDENCE_THRESHOLD) -> None:
        model = model if model != "auto" else os.getenv(FIRE_MODEL_ENV) or "heuristic"
        self._force_heuristic = model == "heuristic"
        self.model_ref = None if model in ("none", "off", "heuristic") else model
        self.conf = conf
        self._model: YOLO | None = None
        self._failed = False
        self._lock = threading.Lock()

    def _ensure_model(self) -> YOLO | None:
        if self._force_heuristic or self._failed or not self.model_ref:
            return None
        if self._model is None:
            with self._lock:
                if self._model is None:
                    try:
                        self._model = YOLO(self._resolve_weights(self.model_ref))
                    except Exception as exc:  # noqa: BLE001 — fall back to heuristic
                        logger.warning(
                            "Fire YOLO model failed to load (%s); using colour heuristic", exc
                        )
                        self._failed = True
        return self._model

    @staticmethod
    def _resolve_weights(ref: str) -> str:
        """Turn a URL / path / model name into a local weights file.

        Note: attempt_download_asset turns 'https://...' into a WindowsPath and
        mangles forward slashes into backslashes on Windows, so URLs are
        downloaded here explicitly.
        """
        if ref.startswith(("http://", "https://")):
            filename = urllib.parse.unquote(ref.rstrip("/").split("/")[-1])
            local = os.path.join("weights", filename)
            os.makedirs(os.path.dirname(local) or ".", exist_ok=True)
            if not os.path.exists(local) or os.path.getsize(local) < 1e5:
                logger.info("Downloading fire model from %s", ref)
                safe_download(url=ref, file=local, min_bytes=1e5)
            return local
        # Local path or a name ultralytics can fetch itself (e.g. yolo11n.pt).
        return attempt_download_asset(ref)

    def detect(self, frame: np.ndarray) -> list[Detection]:
        model = self._ensure_model()
        if model is not None:
            return self._detect_model(model, frame)
        return self._detect_heuristic(frame)

    def _detect_model(self, model: YOLO, frame: np.ndarray) -> list[Detection]:
        results = model.predict(source=frame, conf=self.conf, verbose=False)
        detections: list[Detection] = []
        if results and results[0].boxes is not None:
            names = model.names
            for box in results[0].boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                cls = int(box.cls[0])
                detections.append(
                    Detection(
                        label=names.get(cls, f"class_{cls}"),
                        confidence=float(box.conf[0]),
                        bbox=[x1, y1, x2, y2],
                        source="fire",
                    )
                )
        return detections

    @staticmethod
    def _detect_heuristic(frame: np.ndarray) -> list[Detection]:
        """Bounding box around orange/yellow "hot" pixels — no model needed."""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, _FIRE_HSV_LOW, _FIRE_HSV_HIGH)
        if not np.any(mask):
            return []
        ys, xs = np.nonzero(mask)
        ratio = int((mask > 0).sum()) / mask.size
        return [
            Detection(
                label="fire",
                confidence=round(min(0.99, ratio * 50 + 0.5), 3),
                bbox=[float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())],
                source="fire",
            )
        ]
