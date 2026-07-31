"""VLM gate — decides whether the expensive VLM tier should run.

The whole point of the cheap object-detector tier is to avoid calling
the VLM on every input. The gate enforces that:

  - "gated" (default): the VLM runs only when a cheap trigger fires — the
    object detector found anything.
    Otherwise the VLM is skipped and a cheap result is returned.
  - "always": run the VLM for every input (demo mode).

Policy is chosen via the CAMERA_AI_VLM_POLICY env var (or the constructor).
"""
from __future__ import annotations

import os

from .schemas import Detection

DEFAULT_POLICY = "gated"


class VLMGate:
    def __init__(self, policy: str | None = None) -> None:
        policy = (
            policy
            or os.getenv("CAMERA_AI_VLM_POLICY")
            or DEFAULT_POLICY
        ).strip().lower()
        if policy not in ("gated", "always"):
            policy = DEFAULT_POLICY
        self.policy = policy

    def decide(self, detections: list[Detection]) -> bool:
        """Return True when the VLM tier should run."""
        if self.policy == "always":
            return True
        # gated: any cheap trigger wakes the VLM.
        return bool(detections)

    def __repr__(self) -> str:
        return f"VLMGate(policy={self.policy!r})"
