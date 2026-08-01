import numpy as np

from camera_ai import SecurityAIPipeline
from camera_ai.schemas import EventObject, MediaType
from test_video_pipeline import RecordingDetector, RecordingVLM, write_test_video


def test_stream_video_windows_emits_each_window_in_order(tmp_path):
    calm = np.zeros((64, 64, 3), dtype=np.uint8)
    changed = calm.copy()
    changed[10:40, 10:40] = 255
    path = tmp_path / "stream.mp4"
    write_test_video(path, [calm, changed, calm, calm, calm] * 2, fps=1.0)
    pipeline = SecurityAIPipeline(
        detector=RecordingDetector(), vlm=RecordingVLM(), motion_fps=1.0,
        yolo_fps=1.0, window_seconds=5.0, max_keyframes=2,
    )
    emitted = []
    pipeline.stream_video_windows(
        EventObject(image=str(path), media_type=MediaType.VIDEO), emitted.append
    )
    assert [window["window_index"] for window in emitted] == [0, 1]
    assert all(len(window["frames"]) <= 2 for window in emitted)
