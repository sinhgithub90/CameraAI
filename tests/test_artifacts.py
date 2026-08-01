import json

import numpy as np

from camera_ai.artifacts import WindowArtifactWriter
from camera_ai.event_models import CandidateEvent
from camera_ai.schemas import MotionResult, VideoWindowObservation


def test_writer_creates_debug_bundle_without_embedding_frame_bytes(tmp_path):
    observation = VideoWindowObservation(
        camera_id="cam_01",
        window_id="cam_01_000001",
        start_ms=0,
        end_ms=5000,
        motion=MotionResult(motion=True, score=0.5),
        selected_frames=[1],
    )
    candidate = CandidateEvent(
        candidate_id="candidate_1",
        window_id=observation.window_id,
        candidate_type="unknown_motion",
    )

    directory = WindowArtifactWriter(tmp_path).write(
        observation=observation,
        candidates=[candidate],
        frames=[np.zeros((8, 8, 3), dtype=np.uint8)],
        prompt="verify unknown motion",
        raw_output='{"decision":"uncertain"}',
    )

    assert (directory / "observation.json").exists()
    assert (directory / "candidate.json").exists()
    assert (directory / "selected_frame_01.jpg").exists()
    assert (directory / "vlm_prompt.txt").read_text(encoding="utf-8") == "verify unknown motion"
    payload = json.loads((directory / "observation.json").read_text(encoding="utf-8"))
    assert "frame" not in payload
