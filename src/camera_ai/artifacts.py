"""Optional per-window diagnostic artifact output."""
from __future__ import annotations

from pathlib import Path

import cv2


class WindowArtifactWriter:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @staticmethod
    def _atomic_text(path: Path, value: str) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(value, encoding="utf-8")
        temporary.replace(path)

    def write(
        self,
        *,
        observation,
        candidates,
        frames,
        prompt: str,
        raw_output: str,
        decision=None,
        alert=None,
    ) -> Path:
        directory = self.root / observation.camera_id / observation.window_id
        directory.mkdir(parents=True, exist_ok=True)
        self._atomic_text(directory / "observation.json", observation.model_dump_json(indent=2))
        self._atomic_text(
            directory / "candidate.json",
            "[\n" + ",\n".join(item.model_dump_json(indent=2) for item in candidates) + "\n]",
        )
        self._atomic_text(directory / "vlm_prompt.txt", prompt)
        self._atomic_text(directory / "vlm_raw_output.txt", raw_output)
        self._atomic_text(
            directory / "decision.json",
            decision.model_dump_json(indent=2) if decision is not None else "null",
        )
        self._atomic_text(
            directory / "alert.json",
            alert.model_dump_json(indent=2) if alert is not None else "null",
        )
        for index, frame in enumerate(frames, start=1):
            ok, encoded = cv2.imencode(".jpg", frame)
            if not ok:
                raise RuntimeError("failed to encode selected frame")
            path = directory / f"selected_frame_{index:02d}.jpg"
            temporary = path.with_suffix(".jpg.tmp")
            temporary.write_bytes(encoded.tobytes())
            temporary.replace(path)
        return directory
