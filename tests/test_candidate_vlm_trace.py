import numpy as np

from camera_ai.event_models import CandidateEvent, Priority
from camera_ai.vlm.ollama_qwen import OllamaQwenAnalyzer


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": self.content}}


def candidate(candidate_type="person_only_activity", priority=Priority.LOW):
    return CandidateEvent(
        candidate_id="candidate-1",
        window_id="window-1",
        candidate_type=candidate_type,
        evidence={"person_count": 1},
        priority=priority,
    )


def test_candidate_trace_uses_compact_verification_prompt(monkeypatch):
    captured = {}
    content = '{"decision":"yes","summary":"Có người hoạt động."}'
    monkeypatch.setattr(
        "camera_ai.vlm.ollama_qwen.requests.post",
        lambda url, **kwargs: captured.update(kwargs) or FakeResponse(content),
    )

    trace = OllamaQwenAnalyzer().analyze_with_trace(
        [np.zeros((32, 32, 3), dtype=np.uint8)], [], candidate=candidate()
    )

    prompt = captured["json"]["messages"][0]["content"]
    schema = captured["json"]["format"]
    assert "person_only_activity" in prompt
    assert "yes | no | uncertain" in prompt
    assert "một câu ngắn" in prompt
    assert "risks" not in prompt
    assert "recommended_action" not in prompt
    assert set(schema["required"]) == {"decision", "summary"}
    assert set(schema["properties"]) == {"decision", "summary"}
    assert trace.raw_output_valid is True
    assert trace.decision == "yes"
    assert trace.event_type == "person_only_activity"
    assert trace.evidence == []
    assert trace.scene.risks == []
    assert trace.scene.alert_level.value == "low"
    assert trace.raw_output == content


def test_medium_candidate_yes_maps_to_medium_scene(monkeypatch):
    monkeypatch.setattr(
        "camera_ai.vlm.ollama_qwen.requests.post",
        lambda url, **kwargs: FakeResponse(
            '{"decision":"yes","summary":"Có tương tác với xe."}'
        ),
    )

    trace = OllamaQwenAnalyzer().analyze_with_trace(
        [np.zeros((32, 32, 3), dtype=np.uint8)],
        [],
        candidate=candidate(
            "possible_person_vehicle_interaction", Priority.MEDIUM
        ),
    )

    assert trace.scene.alert_level.value == "medium"
    assert trace.scene.recommended_action == "Kiểm tra sự kiện trên camera."


def test_valid_uncertain_is_low_without_degraded_scene(monkeypatch):
    monkeypatch.setattr(
        "camera_ai.vlm.ollama_qwen.requests.post",
        lambda url, **kwargs: FakeResponse(
            '{"decision":"uncertain","summary":"Hình ảnh chưa đủ rõ."}'
        ),
    )

    trace = OllamaQwenAnalyzer().analyze_with_trace(
        [np.zeros((32, 32, 3), dtype=np.uint8)], [], candidate=candidate()
    )

    assert trace.raw_output_valid is True
    assert trace.decision == "uncertain"
    assert trace.scene.alert_level.value == "low"
    assert trace.scene.degraded is False
    assert trace.scene.recommended_action == "Kiểm tra lại hình ảnh."


def test_truncated_candidate_trace_is_uncertain_and_invalid(monkeypatch):
    content = '{"decision":"yes","summary":"Có lửa"'
    monkeypatch.setattr(
        "camera_ai.vlm.ollama_qwen.requests.post",
        lambda url, **kwargs: FakeResponse(content),
    )

    trace = OllamaQwenAnalyzer().analyze_with_trace(
        [np.zeros((32, 32, 3), dtype=np.uint8)],
        [],
        candidate=candidate("possible_fire_visual_change"),
    )

    assert trace.raw_output_valid is False
    assert trace.decision == "uncertain"
    assert trace.scene.degraded is True
    assert trace.scene.alert_level.value == "low"
