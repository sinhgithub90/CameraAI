"""The demo page submits a selected video batch through the batch API."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_batch_ui_posts_all_videos_and_renders_analysis_rows():
    result = subprocess.run(
        ["node", "tests/ui_batch_harness.mjs"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    observed = json.loads(result.stdout)
    assert observed["endpoint"] == "/async/analyze/videos"
    assert observed["field_names"] == ["files", "files"]
    assert observed["row_count"] == 2
    assert observed["detail_title"] == "Chi tiết video: cam-a.mp4"
