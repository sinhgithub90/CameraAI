"""Smoke tests for the core pipeline, no external services needed.

Uses dummy detectors + MockAnalyzer so tests run without YOLO weights or a
running Ollama instance.
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


def test_pipeline_does_not_construct_fire_detector(monkeypatch):
    class UnexpectedFireDetector:
        def __init__(self):
            raise AssertionError("runtime pipeline must not construct FireDetector")

    monkeypatch.setattr(
        "camera_ai.pipeline.FireDetector",
        UnexpectedFireDetector,
        raising=False,
    )

    SecurityAIPipeline(detector=EmptyDetector(), vlm=MockAnalyzer())


def _make_pipeline(detector=EmptyDetector(), gate=None) -> SecurityAIPipeline:
    return SecurityAIPipeline(
        detector=detector,
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


def test_detect_image_returns_pending_status():
    """detect() should return vlm.status='pending', not call VLM."""
    from camera_ai.pipeline import SecurityAIPipeline

    pipeline = SecurityAIPipeline(
        detector=DummyDetector(),
        vlm=MockAnalyzer(),
    )
    result = pipeline.detect(
        EventObject(image=_png_bytes(), camera_id="async_cam", media_type=MediaType.IMAGE)
    )
    assert result.vlm.status == "pending"
    assert result.vlm.skipped is False
    assert result.detections  # detection vẫn có


def test_detect_skips_vlm_when_gate_closed():
    """detect() with no detections should still return vlm.status='skipped'."""
    from camera_ai.pipeline import SecurityAIPipeline

    pipeline = SecurityAIPipeline(
        detector=EmptyDetector(),
        vlm=MockAnalyzer(),
    )
    result = pipeline.detect(
        EventObject(image=_png_bytes(), media_type=MediaType.IMAGE)
    )
    assert result.vlm.status == "skipped"
    assert result.vlm.skipped is True


def test_analyze_vlm_runs_on_task():
    """analyze_vlm() takes a VLMTask and returns SceneAnalysis."""
    from camera_ai.pipeline import SecurityAIPipeline
    from camera_ai.queue import VLMTask
    import time

    pipeline = SecurityAIPipeline(
        detector=DummyDetector(),
        vlm=MockAnalyzer(),
    )
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    task = VLMTask(
        task_id="test-task",
        camera_id="cam_01",
        alert_id="alert-1",
        frames=[frame],
        detections=[Detection(label="person", confidence=0.91, bbox=[1, 2, 3, 4])],
        rule_id="test_rule",
        priority=3,
        enqueued_at=time.monotonic(),
        max_keyframes=2,
    )
    analysis = pipeline.analyze_vlm(task)
    assert analysis.alert_level.value in ("low", "medium", "high")
    assert analysis.degraded is True  # mock


def test_analyze_event_still_works_backward_compat():
    """Existing analyze_event() should still work (backward compat)."""
    result = _make_pipeline(detector=DummyDetector()).analyze_event(
        EventObject(image=_png_bytes(), camera_id="cam_legacy", media_type=MediaType.IMAGE)
    )
    assert result.vlm.status == "completed"  # đồng bộ → completed ngay
    assert result.vlm.degraded is True
