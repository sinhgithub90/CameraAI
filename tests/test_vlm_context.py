from __future__ import annotations

import numpy as np

from camera_ai.vlm.ollama_qwen import OllamaQwenAnalyzer


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": '{"summary":"ok"}'}}


def test_ollama_requests_context_large_enough_for_keyframes(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr("camera_ai.vlm.ollama_qwen.requests.post", fake_post)
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    OllamaQwenAnalyzer(num_ctx=8192).analyze_sequence([frame] * 8, [])

    assert captured["json"]["options"]["num_ctx"] == 8192
