from __future__ import annotations

from camera_ai.detectors.yolo import YOLODetector
from camera_ai.vlm.ollama_qwen import OllamaQwenAnalyzer


def test_yolo_default_is_yolo26n(monkeypatch):
    monkeypatch.delenv("YOLO_WEIGHTS", raising=False)
    assert YOLODetector().weights == "yolo26n.pt"


def test_yolo_weights_can_be_overridden(monkeypatch):
    monkeypatch.setenv("YOLO_WEIGHTS", "custom.pt")
    assert YOLODetector().weights == "custom.pt"


def test_qwen_default_is_local_4b_model(monkeypatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    assert OllamaQwenAnalyzer().model == "qwen3-vl:4b-instruct-q4_K_M"
