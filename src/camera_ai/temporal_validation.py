"""Temporal confirmation for specialized detector signals."""
from __future__ import annotations

from pydantic import BaseModel, Field

from .schemas import Detection


class TemporalSignal(BaseModel):
    confirmed: bool = False
    consecutive: int = 0
    detections: list[Detection] = Field(default_factory=list)


class TemporalSignalValidator:
    def __init__(self, min_consecutive: int = 3, max_gap_frames: int = 1) -> None:
        self.min_consecutive = min_consecutive
        self.max_gap_frames = max_gap_frames
        self._consecutive = 0
        self._last_positive: int | None = None

    def update(
        self, frame_index: int, detections: list[Detection]
    ) -> TemporalSignal:
        positives = [item for item in detections if item.label in {"fire", "smoke"}]
        if not positives:
            self._consecutive = 0
            self._last_positive = None
            return TemporalSignal()
        if (
            self._last_positive is None
            or frame_index - self._last_positive <= self.max_gap_frames + 1
        ):
            self._consecutive += 1
        else:
            self._consecutive = 1
        self._last_positive = frame_index
        return TemporalSignal(
            confirmed=self._consecutive >= self.min_consecutive,
            consecutive=self._consecutive,
            detections=positives,
        )
