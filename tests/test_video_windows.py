from pathlib import Path

import numpy as np

from camera_ai.schemas import VideoFrameObservation
import camera_ai.video_windows as video_windows
from camera_ai.video_windows import RawVideoWindow


def test_raw_video_window_is_independent_of_queue_and_store():
    window = RawVideoWindow(
        window_index=0,
        start_seconds=0.0,
        observations=[VideoFrameObservation(frame_index=0, frame=np.zeros((8, 8, 3), dtype=np.uint8))],
    )
    assert window.end_seconds == 5.0
    assert "queue" not in RawVideoWindow.__module__


def test_video_window_module_does_not_depend_on_queue_or_store():
    source = Path(video_windows.__file__).read_text(encoding="utf-8")
    assert "from .queue" not in source
    assert "from .analysis_store" not in source
