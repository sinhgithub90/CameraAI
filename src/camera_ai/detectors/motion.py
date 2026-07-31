"""Lightweight frame-to-frame motion detection using OpenCV."""
from __future__ import annotations

import cv2
import numpy as np

from ..schemas import MotionRegion, MotionResult


class MotionDetector:
    """Detect changed image regions without classifying their contents."""

    def __init__(
        self,
        threshold: float = 0.02,
        pixel_threshold: int = 25,
        min_region_area: int = 16,
    ) -> None:
        self.threshold = threshold
        self.pixel_threshold = pixel_threshold
        self.min_region_area = min_region_area
        self._previous_gray: np.ndarray | None = None

    def compare(self, frame: np.ndarray) -> MotionResult:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        previous = self._previous_gray
        self._previous_gray = gray
        if previous is None or previous.shape != gray.shape:
            return MotionResult()

        difference = cv2.absdiff(previous, gray)
        mask = cv2.threshold(
            difference, self.pixel_threshold, 255, cv2.THRESH_BINARY
        )[1]
        kernel = np.ones((3, 3), dtype=np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.dilate(mask, kernel, iterations=2)
        changed_ratio = float(np.count_nonzero(mask)) / float(mask.size)
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        regions: list[MotionRegion] = []
        for contour in contours:
            x, y, width, height = cv2.boundingRect(contour)
            if width * height >= self.min_region_area:
                regions.append(MotionRegion(x=x, y=y, w=width, h=height))
        regions.sort(key=lambda region: region.w * region.h, reverse=True)
        score = min(1.0, changed_ratio * 5.0)
        return MotionResult(
            motion=changed_ratio >= self.threshold and bool(regions),
            changed_ratio=changed_ratio,
            regions=regions,
            score=score,
        )

    def reset(self) -> None:
        self._previous_gray = None
