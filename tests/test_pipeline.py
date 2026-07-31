"""Smoke tests for the core pipeline, no external services needed.

Uses dummy detectors + MockAnalyzer so tests run without YOLO weights, fire
model downloads, or a running Ollama instance.
"""
from __future__ import annotations

import cv2
import numpy as np

from camera_ai import SecurityAIPipeline
from camera_ai.detectors.fire import FireDetector
from camera_ai.gate import VLMGate
from camera_ai.schemas import AlertLevel, Detection, EventObject, MediaType
from camera_ai.vlm.mock import MockAnalyzer


class DummyDetector:
    def detect(self, frame: np.ndarray) -> list[Detection]:
        return [Detection(label="person", confidence=0.91, bbox=[1, 2, 3, 4])]


class EmptyDetector:
    def detect(self, frame: np.ndarray) -> list[Detection]:
        return []


class NoFireDetector:
    def detect(self, frame: np.ndarray) -> list[Detection]:
        return []


def _make_pipeline(detector=EmptyDetector(), gate=None) -> SecurityAIPipeline:
    return SecurityAIPipeline(
        detector=detector,
        fire_detector=NoFireDetector(),
        vlm=MockAnalyzer(),
        gate=gate,
    )


def _png_bytes() -> bytes:
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    ok, buf = cv2.imencode(".png", frame)
    assert ok
    return buf.tobytes()


def test_image_pipeline_runs_vlm_on_trigger():
    result = _make_pipeline(detector=DummyDetector()).analyze_event(
        EventObject(image=_png_bytes(), camera_id="cam_test", media_type=MediaType.IMAGE)
    )
    assert result.media_type == MediaType.IMAGE
    assert result.camera_id == "cam_test"
    assert result.detections[0].label == "person"
    assert result.detections[0].confidence == 0.91
    assert result.vlm.skipped is False
    assert result.vlm.degraded is True  # mock marks itself
    assert result.security.alert_level.value in ("low", "medium", "high")
    assert result.security.risks
    assert result.request_id


def test_gate_skips_vlm_when_no_trigger():
    result = _make_pipeline().analyze_event(
        EventObject(image=_png_bytes(), camera_id="cam_calm", media_type=MediaType.IMAGE)
    )
    assert result.vlm.skipped is True
    assert result.security.alert_level == AlertLevel.LOW
    assert result.detections == []


def test_gate_always_policy_runs_vlm():
    result = _make_pipeline(gate=VLMGate(policy="always")).analyze_event(
        EventObject(image=_png_bytes(), media_type=MediaType.IMAGE)
    )
    assert result.vlm.skipped is False
    assert result.vlm.degraded is True


def test_annotate_always_returns_image_even_without_detections():
    result = _make_pipeline().analyze_event(
        EventObject(image=_png_bytes(), media_type=MediaType.IMAGE)
    )
    assert result.annotated_image  # no detections but still an image


def test_image_from_path(tmp_path):
    path = tmp_path / "frame.png"
    ok, buf = cv2.imencode(".png", np.zeros((64, 64, 3), dtype=np.uint8))
    assert ok
    path.write_bytes(buf.tobytes())

    result = _make_pipeline(detector=DummyDetector()).analyze_event(
        EventObject(image=str(path), camera_id="cam_path", media_type=MediaType.IMAGE)
    )
    assert result.detections


def test_fire_heuristic_detector():
    detector = FireDetector(model="heuristic")
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[40:60, 40:60] = (0, 80, 255)  # BGR orange block
    dets = detector.detect(frame)
    assert dets
    assert dets[0].source == "fire"
    assert dets[0].label == "fire"


def test_fire_heuristic_empty_on_plain_frame():
    detector = FireDetector(model="heuristic")
    dets = detector.detect(np.zeros((100, 100, 3), dtype=np.uint8))
    assert dets == []


def test_bad_image_bytes_raises():
    try:
        _make_pipeline(detector=DummyDetector()).analyze_event(
            EventObject(image=b"not-an-image", media_type=MediaType.IMAGE)
        )
    except ValueError:
        return
    raise AssertionError("expected ValueError for undecodable image bytes")
