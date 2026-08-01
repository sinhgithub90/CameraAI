"""The demo UI polls every async video window alert."""
from pathlib import Path


def test_async_ui_passes_all_video_alert_ids_to_polling():
    source = (
        Path(__file__).resolve().parents[1]
        / "apps"
        / "api"
        / "static"
        / "index.html"
    ).read_text(encoding="utf-8")

    assert "startPoll(data.alert_ids || [data.request_id], data)" in source
    assert "Promise.all(alertIds.map" in source
