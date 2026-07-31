from __future__ import annotations

import logging

import numpy as np

from camera_ai.schemas import Detection
from camera_ai.vlm.ollama_qwen import OllamaQwenAnalyzer


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "message": {"content": '{"summary":"ok"}'},
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
        "num_predict": 160,
        "temperature": 0,
    }
    assert captured["json"]["keep_alive"] == "10m"


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


def test_detection_prompt_keeps_top_three_per_label_and_twelve_total():
    detections = []
    for label in ("person", "car", "dog", "truck"):
        for confidence in (0.40, 0.95, 0.70, 0.80):
            detections.append(
                Detection(
                    label=label,
                    confidence=confidence,
                    bbox=[10, 20, 30, 40],
                )
            )
    detections.append(
        Detection(label="bicycle", confidence=0.99, bbox=[1, 2, 3, 4])
    )

    lines = OllamaQwenAnalyzer._format_detections(detections).splitlines()

    assert len(lines) == 12
    assert [line.split()[1] for line in lines] == [
        "person",
        "person",
        "person",
        "car",
        "car",
        "car",
        "dog",
        "dog",
        "dog",
        "truck",
        "truck",
        "truck",
    ]
    assert all("conf=0.40" not in line for line in lines)
    assert OllamaQwenAnalyzer._format_detections([]) == "- none"
