from __future__ import annotations

import logging

import numpy as np

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


def test_ollama_sends_two_images_with_compact_json_schema(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("camera_ai.vlm.ollama_qwen.requests.post", fake_post)
    frame = np.zeros((64, 64, 3), dtype=np.uint8)

    OllamaQwenAnalyzer().analyze_sequence([frame, frame], [])

    payload = captured["json"]
    assert len(payload["messages"][0]["images"]) == 2
    assert payload["format"]["type"] == "object"
    assert set(payload["format"]["required"]) == {
        "alert_level",
        "summary",
        "risks",
        "recommended_action",
    }
    assert payload["format"]["additionalProperties"] is False


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
