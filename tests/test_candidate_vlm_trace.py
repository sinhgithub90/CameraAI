import numpy as np
import pytest

from camera_ai.event_models import CandidateEvent, Priority
from camera_ai.schemas import Detection
from camera_ai.vlm.ollama_qwen import OllamaQwenAnalyzer


class FakeResponse:
    def __init__(self, content):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": self.content}}


def candidate(candidate_type="person_scene", priority=Priority.LOW):
    return CandidateEvent(
        candidate_id="candidate-1",
        window_id="window-1",
        candidate_type=candidate_type,
        evidence={"secret_router_signal": 99},
        priority=priority,
    )


def test_generic_candidate_prompt_uses_only_visual_context(monkeypatch):
    captured = {}
    content = (
        '{"decision":"yes","event_type":"person_vehicle_interaction",'
        '"summary":"Có người hoạt động gần phương tiện."}'
    )
    monkeypatch.setattr(
        "camera_ai.vlm.ollama_qwen.requests.post",
        lambda url, **kwargs: captured.update(kwargs) or FakeResponse(content),
    )

    trace = OllamaQwenAnalyzer().analyze_with_trace(
        [np.zeros((32, 32, 3), dtype=np.uint8)],
        [
            Detection(
                label="forklift_secret",
                confidence=0.87,
                bbox=[1, 2, 3, 4],
            )
        ],
        candidate=candidate(),
    )

    prompt = captured["json"]["messages"][0]["content"]
    schema = captured["json"]["format"]
    assert "Phân tích trực tiếp hình ảnh" in prompt
    assert "yes | no | uncertain" in prompt
    assert "1–2 câu" in prompt
    assert "person_scene" not in prompt
    assert "secret_router_signal" not in prompt
    assert "forklift_secret" not in prompt
    assert "count=" not in prompt
    assert "max_conf=" not in prompt
    assert "tiếp xúc hoặc chồng lấn" not in prompt
    assert "risks" not in prompt
    assert "recommended_action" not in prompt
    assert set(schema["required"]) == {"decision", "event_type", "summary"}
    assert set(schema["properties"]) == {"decision", "event_type", "summary"}
    assert set(schema["properties"]["event_type"]["enum"]) == {
        "no_event",
        "person_vehicle_interaction",
        "traffic_accident",
        "person_fall",
        "fighting",
        "fire_smoke",
        "camera_tamper",
        "unknown_event",
    }
    assert trace.raw_output_valid is True
    assert trace.decision == "yes"
    assert trace.event_type == "person_vehicle_interaction"
    assert trace.evidence == []
    assert trace.scene.risks == []
    assert trace.scene.alert_level.value == "low"
    assert trace.raw_output == content


@pytest.mark.parametrize("candidate_type", ["vehicle_scene", "person_vehicle_scene"])
def test_traffic_candidate_selects_specialized_visual_prompt(
    monkeypatch, candidate_type
):
    captured = {}
    monkeypatch.setattr(
        "camera_ai.vlm.ollama_qwen.requests.post",
        lambda url, **kwargs: captured.update(kwargs)
        or FakeResponse(
            '{"decision":"yes","event_type":"traffic_accident",'
            '"summary":"Hai phương tiện va chạm."}'
        ),
    )
    frames = [np.zeros((32, 32, 3), dtype=np.uint8) for _ in range(2)]

    OllamaQwenAnalyzer().analyze_with_trace(
        frames,
        [],
        candidate=candidate(candidate_type),
    )

    prompt = captured["json"]["messages"][0]["content"]
    assert "So sánh trạng thái TRƯỚC và SAU trong cảnh giao thông" in prompt
    assert "tách rời chuyển thành tiếp xúc hoặc chồng lấn" in prompt
    assert "dừng ở vị trí tương đối bất thường" in prompt
    assert "Không bắt buộc nhìn thấy đúng khoảnh khắc va chạm" in prompt
    assert "Chỉ chọn person_vehicle_interaction" in prompt
    assert candidate_type not in prompt


def test_medium_candidate_yes_maps_to_medium_scene(monkeypatch):
    monkeypatch.setattr(
        "camera_ai.vlm.ollama_qwen.requests.post",
        lambda url, **kwargs: FakeResponse(
            '{"decision":"yes","event_type":"traffic_accident",'
            '"summary":"Có va chạm giữa các phương tiện."}'
        ),
    )

    trace = OllamaQwenAnalyzer().analyze_with_trace(
        [np.zeros((32, 32, 3), dtype=np.uint8)],
        [],
        candidate=candidate(
            "person_vehicle_scene", Priority.MEDIUM
        ),
    )

    assert trace.scene.alert_level.value == "medium"
    assert trace.event_type == "traffic_accident"
    assert trace.scene.recommended_action == "Kiểm tra sự kiện trên camera."


def test_valid_uncertain_is_low_without_degraded_scene(monkeypatch):
    monkeypatch.setattr(
        "camera_ai.vlm.ollama_qwen.requests.post",
        lambda url, **kwargs: FakeResponse(
            '{"decision":"uncertain","event_type":"unknown_event",'
            '"summary":"Hình ảnh chưa đủ rõ."}'
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
    assert trace.event_type == "unknown_event"


def test_valid_no_uses_no_event(monkeypatch):
    monkeypatch.setattr(
        "camera_ai.vlm.ollama_qwen.requests.post",
        lambda url, **kwargs: FakeResponse(
            '{"decision":"no","event_type":"no_event",'
            '"summary":"Không có sự kiện bất thường."}'
        ),
    )

    trace = OllamaQwenAnalyzer().analyze_with_trace(
        [np.zeros((32, 32, 3), dtype=np.uint8)], [], candidate=candidate()
    )

    assert trace.raw_output_valid is True
    assert trace.decision == "no"
    assert trace.event_type == "no_event"


@pytest.mark.parametrize(
    ("decision", "event_type"),
    [
        ("no", "traffic_accident"),
        ("yes", "no_event"),
        ("uncertain", "person_fall"),
        ("yes", "vehicle_collision"),
    ],
)
def test_inconsistent_or_unknown_event_type_is_invalid(
    monkeypatch, decision, event_type
):
    monkeypatch.setattr(
        "camera_ai.vlm.ollama_qwen.requests.post",
        lambda url, **kwargs: FakeResponse(
            '{"decision":"%s","event_type":"%s","summary":"Kết quả."}'
            % (decision, event_type)
        ),
    )

    trace = OllamaQwenAnalyzer().analyze_with_trace(
        [np.zeros((32, 32, 3), dtype=np.uint8)], [], candidate=candidate()
    )

    assert trace.raw_output_valid is False
    assert trace.decision == "uncertain"
    assert trace.event_type == "unknown_event"
    assert trace.scene.alert_level.value == "low"
    assert trace.scene.degraded is True


def test_truncated_candidate_trace_is_uncertain_and_invalid(monkeypatch):
    content = '{"decision":"yes","summary":"Có lửa"'
    monkeypatch.setattr(
        "camera_ai.vlm.ollama_qwen.requests.post",
        lambda url, **kwargs: FakeResponse(content),
    )

    trace = OllamaQwenAnalyzer().analyze_with_trace(
        [np.zeros((32, 32, 3), dtype=np.uint8)],
        [],
        candidate=candidate("temporally_confirmed_fire_signal"),
    )

    assert trace.raw_output_valid is False
    assert trace.decision == "uncertain"
    assert trace.scene.degraded is True
    assert trace.scene.alert_level.value == "low"
    assert trace.event_type == "unknown_event"
