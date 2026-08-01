from __future__ import annotations

import logging

import numpy as np
import pytest

from camera_ai.schemas import Detection
from camera_ai.vlm.ollama_qwen import OllamaQwenAnalyzer


class FakeResponse:
    def __init__(
        self,
        content: str = (
            '{"alert_level":"low","summary":"Bình thường.","risks":[],'
            '"recommended_action":"Tiếp tục giám sát."}'
        ),
    ):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "message": {"content": self.content},
            "total_duration": 5_000_000_000,
            "load_duration": 1_000_000_000,
            "prompt_eval_count": 123,
            "prompt_eval_duration": 2_000_000_000,
            "eval_count": 45,
            "eval_duration": 1_500_000_000,
        }


def test_composite_mode_is_default_and_can_be_overridden(monkeypatch):
    monkeypatch.delenv("OLLAMA_FRAME_MODE", raising=False)
    assert OllamaQwenAnalyzer().frame_mode == "composite"

    monkeypatch.setenv("OLLAMA_FRAME_MODE", "separate")
    assert OllamaQwenAnalyzer().frame_mode == "separate"


def test_invalid_frame_mode_fails_fast():
    with pytest.raises(ValueError, match="OLLAMA_FRAME_MODE"):
        OllamaQwenAnalyzer(frame_mode="unknown")


def test_two_frame_composite_preserves_order_and_dimensions(monkeypatch):
    monkeypatch.delenv("OLLAMA_FRAME_MODE", raising=False)
    before = np.full((360, 640, 3), (10, 20, 230), dtype=np.uint8)
    after = np.full((360, 640, 3), (40, 210, 30), dtype=np.uint8)

    composite = OllamaQwenAnalyzer()._compose_two_frames([before, after])

    assert composite.shape == (1080, 960, 3)
    assert np.all(composite[270, 480] == before[0, 0])
    assert np.all(composite[810, 480] == after[0, 0])


def test_composite_letterboxes_portrait_frames(monkeypatch):
    monkeypatch.delenv("OLLAMA_FRAME_MODE", raising=False)
    portrait = np.full((800, 400, 3), 255, dtype=np.uint8)

    composite = OllamaQwenAnalyzer()._compose_two_frames([portrait, portrait])

    assert np.all(composite[270, 10] == 0)
    assert np.all(composite[270, 480] == 255)


def test_composite_labels_both_temporal_panels(monkeypatch):
    monkeypatch.delenv("OLLAMA_FRAME_MODE", raising=False)
    black = np.zeros((360, 640, 3), dtype=np.uint8)

    composite = OllamaQwenAnalyzer()._compose_two_frames([black, black])

    assert np.any(composite[5:40, 5:145] != 0)
    assert np.any(composite[545:580, 5:145] != 0)


@pytest.mark.parametrize("count", [1, 3])
def test_composite_mode_preserves_non_two_frame_counts(monkeypatch, count):
    monkeypatch.delenv("OLLAMA_FRAME_MODE", raising=False)
    frames = [np.zeros((64, 64, 3), dtype=np.uint8) for _ in range(count)]

    assert len(OllamaQwenAnalyzer()._prepare_images(frames)) == count


def test_ollama_requests_context_large_enough_for_keyframes(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("camera_ai.vlm.ollama_qwen.requests.post", fake_post)
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    OllamaQwenAnalyzer(num_ctx=8192).analyze_sequence([frame] * 8, [])

    assert captured["json"]["options"]["num_ctx"] == 8192


def test_ollama_uses_gpu_friendly_request_defaults(monkeypatch):
    captured = {}
    monkeypatch.delenv("OLLAMA_NUM_CTX", raising=False)
    monkeypatch.delenv("OLLAMA_NUM_PREDICT", raising=False)
    monkeypatch.delenv("OLLAMA_KEEP_ALIVE", raising=False)

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("camera_ai.vlm.ollama_qwen.requests.post", fake_post)
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    OllamaQwenAnalyzer().analyze_sequence([frame] * 4, [])

    assert captured["json"]["options"] == {
        "num_ctx": 4096,
        "num_predict": 96,
        "temperature": 0,
    }
    assert captured["json"]["keep_alive"] == "10m"


def test_ollama_sends_one_composite_with_compact_json_schema(monkeypatch):
    captured = {}
    monkeypatch.delenv("OLLAMA_FRAME_MODE", raising=False)

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("camera_ai.vlm.ollama_qwen.requests.post", fake_post)
    frame = np.zeros((64, 64, 3), dtype=np.uint8)

    OllamaQwenAnalyzer().analyze_sequence([frame, frame], [])

    payload = captured["json"]
    assert len(payload["messages"][0]["images"]) == 1
    assert "nửa trên là TRƯỚC" in payload["messages"][0]["content"]
    assert payload["format"]["type"] == "object"
    assert set(payload["format"]["required"]) == {
        "alert_level",
        "summary",
        "risks",
        "recommended_action",
    }
    assert payload["format"]["additionalProperties"] is False


def test_separate_mode_sends_two_images(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("camera_ai.vlm.ollama_qwen.requests.post", fake_post)
    frame = np.zeros((64, 64, 3), dtype=np.uint8)

    OllamaQwenAnalyzer(frame_mode="separate").analyze_sequence(
        [frame, frame],
        [],
    )

    assert len(captured["json"]["messages"][0]["images"]) == 2


def test_composite_request_logs_frame_telemetry(monkeypatch, caplog):
    monkeypatch.delenv("OLLAMA_FRAME_MODE", raising=False)
    monkeypatch.setattr(
        "camera_ai.vlm.ollama_qwen.requests.post",
        lambda url, **kwargs: FakeResponse(),
    )
    frame = np.zeros((64, 64, 3), dtype=np.uint8)

    with caplog.at_level(logging.INFO, logger="camera_ai.vlm.ollama_qwen"):
        OllamaQwenAnalyzer().analyze_sequence([frame, frame], [])

    assert "frame_mode=composite" in caplog.text
    assert "source_frames=2" in caplog.text
    assert "sent_images=1" in caplog.text
    assert "composite_shape=960x1080" in caplog.text


def test_ollama_request_optimization_can_be_overridden(monkeypatch):
    captured = {}
    monkeypatch.setenv("OLLAMA_NUM_CTX", "6144")
    monkeypatch.setenv("OLLAMA_NUM_PREDICT", "96")
    monkeypatch.setenv("OLLAMA_KEEP_ALIVE", "20m")

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("camera_ai.vlm.ollama_qwen.requests.post", fake_post)
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    OllamaQwenAnalyzer().analyze(frame, [])

    assert captured["json"]["options"] == {
        "num_ctx": 6144,
        "num_predict": 96,
        "temperature": 0,
    }
    assert captured["json"]["keep_alive"] == "20m"


def test_ollama_logs_server_timing_and_token_counts(monkeypatch, caplog):
    monkeypatch.setattr(
        "camera_ai.vlm.ollama_qwen.requests.post",
        lambda url, **kwargs: FakeResponse(),
    )
    frame = np.zeros((64, 64, 3), dtype=np.uint8)

    with caplog.at_level(logging.INFO, logger="camera_ai.vlm.ollama_qwen"):
        OllamaQwenAnalyzer().analyze(frame, [])

    assert "total_ms=5000.0" in caplog.text
    assert "load_ms=1000.0" in caplog.text
    assert "prompt_tokens=123" in caplog.text
    assert "prompt_ms=2000.0" in caplog.text
    assert "output_tokens=45" in caplog.text
    assert "output_ms=1500.0" in caplog.text


def test_detection_prompt_summarizes_count_and_max_confidence_per_label():
    detections = [
        Detection(label="person", confidence=0.55, bbox=[10, 20, 30, 40]),
        Detection(label="person", confidence=0.91, bbox=[11, 21, 31, 41]),
        Detection(label="car", confidence=0.87, bbox=[1, 2, 3, 4]),
    ]

    text = OllamaQwenAnalyzer._format_detections(detections)

    assert text == (
        "- person: count=2, max_conf=0.91\n"
        "- car: count=1, max_conf=0.87"
    )
    assert "bbox" not in text
    assert OllamaQwenAnalyzer._format_detections([]) == "- none"


def test_compact_response_derives_observations_from_summary(monkeypatch):
    monkeypatch.setattr(
        "camera_ai.vlm.ollama_qwen.requests.post",
        lambda url, **kwargs: FakeResponse(),
    )
    frame = np.zeros((64, 64, 3), dtype=np.uint8)

    result = OllamaQwenAnalyzer().analyze(frame, [])

    assert result.summary == "Bình thường."
    assert result.observations == ["Bình thường."]
    assert result.recommended_action == "Tiếp tục giám sát."


def test_empty_qwen_summary_is_exposed_as_degraded_result():
    result = OllamaQwenAnalyzer._parse(
        '{"alert_level":"low","summary":"","risks":[],"recommended_action":""}'
    )

    assert result.degraded is True
    assert result.summary == "VLM không trả nội dung phân tích cho window này."
