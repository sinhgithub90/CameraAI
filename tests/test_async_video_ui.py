"""The demo UI polls every async video window alert."""
from pathlib import Path


def test_async_video_ui_polls_the_aggregate_analysis_endpoint():
    source = (
        Path(__file__).resolve().parents[1]
        / "apps"
        / "api"
        / "static"
        / "index.html"
    ).read_text(encoding="utf-8")

    assert "startVideoPoll(data.request_id, data)" in source
    assert "'/analyses/' + analysisId" in source
    assert "Promise.all(alertIds.map" not in source


def test_video_ui_labels_inherited_red_as_not_reverified():
    source = (
        Path(__file__).resolve().parents[1]
        / "apps"
        / "api"
        / "static"
        / "index.html"
    ).read_text(encoding="utf-8")

    assert "inherited_active_alert" in source
    assert "Chưa được Qwen xác minh lại" in source
    assert "active_alert_cooldown" in source
    assert "Active alert" in source


def test_video_ui_renders_window_vlm_results_and_timing_without_detection_table():
    source = (
        Path(__file__).resolve().parents[1]
        / "apps"
        / "api"
        / "static"
        / "index.html"
    ).read_text(encoding="utf-8")

    assert 'id="window-results"' in source
    assert "function renderVideoWindows" in source
    assert "Motion total" in source
    assert "VLM không trả nội dung phân tích cho window này." in source
    assert "Đối tượng phát hiện" not in source
