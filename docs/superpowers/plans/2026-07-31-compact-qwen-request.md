# Compact Qwen Request Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce warm Qwen-VL latency while retaining Qwen 4B, two separate temporal images, and the current public API contract.

**Architecture:** Keep optimization inside `OllamaQwenAnalyzer`. Send a short prompt plus a strict Ollama JSON schema, aggregate YOLO detections into compact label summaries, and derive legacy `observations` from the returned summary.

**Tech Stack:** Python 3.11+, requests, OpenCV, NumPy, Pydantic, pytest, Ollama `/api/chat`

## Global Constraints

- Keep `qwen3-vl:4b-instruct-q4_K_M` and two separate temporal JPEG images.
- Keep `num_ctx=4096`, `temperature=0`, and existing keep-alive behavior.
- Set default `num_predict=96`; preserve `OLLAMA_NUM_PREDICT` override.
- Do not change `SceneAnalysis`, `PipelineResult`, or endpoint response schemas.
- Preserve existing degraded behavior for Ollama and parsing failures.

---

### Task 1: Lock the compact request contract with tests

**Files:**
- Modify: `tests/test_vlm_context.py`

**Interfaces:**
- Consumes: `OllamaQwenAnalyzer.analyze_sequence(frames, detections)`
- Produces: assertions for the request `format`, options, prompt, detector summary and parsed `SceneAnalysis`

- [ ] **Step 1: Extend the fake response and capture request payload**

Make `FakeResponse` accept compact JSON content, then capture `kwargs["json"]` in focused tests.

```python
class FakeResponse:
    def __init__(self, content='{"alert_level":"low","summary":"Bình thường.","risks":[],"recommended_action":"Tiếp tục giám sát."}'):
        self.content = content

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
```

- [ ] **Step 2: Add failing request-schema and default-option tests**

Assert that the payload keeps two encoded images, uses `num_predict=96`, and includes a top-level object schema with four required fields and `additionalProperties=False`.

```python
assert len(payload["messages"][0]["images"]) == 2
assert payload["options"]["num_predict"] == 96
assert payload["format"]["type"] == "object"
assert set(payload["format"]["required"]) == {
    "alert_level", "summary", "risks", "recommended_action"
}
assert payload["format"]["additionalProperties"] is False
```

- [ ] **Step 3: Add failing detector-compaction and compatibility tests**

Use repeated `person` and `car` detections and assert exact compact lines, no `bbox`, and derived observations.

```python
assert OllamaQwenAnalyzer._format_detections(detections) == (
    "- person: count=2, max_conf=0.91\n"
    "- car: count=1, max_conf=0.87"
)
assert "bbox" not in prompt
assert result.observations == ["Bình thường."]
```

- [ ] **Step 4: Run focused tests and confirm failure**

Run: `python -m pytest tests/test_vlm_context.py -v`

Expected: failures show the old default `160`, missing `format`, bounding-box detector lines, and empty derived observations.

---

### Task 2: Implement the compact Ollama request

**Files:**
- Modify: `src/camera_ai/vlm/ollama_qwen.py`
- Test: `tests/test_vlm_context.py`

**Interfaces:**
- Consumes: `list[Detection]`, two BGR `np.ndarray` frames and Ollama response content
- Produces: `_format_detections(detections) -> str` and backward-compatible `SceneAnalysis`

- [ ] **Step 1: Define the short prompt and JSON schema**

Replace the verbose schema prose with a short Vietnamese instruction and define a module-level schema.

```python
_PROMPT = (
    "Phân tích 2 ảnh camera theo thứ tự thời gian. Trả về cảnh báo an ninh "
    "ngắn gọn bằng tiếng Việt. Dữ liệu YOLO:\n{detections}"
)

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "alert_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "summary": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "recommended_action": {"type": "string"},
    },
    "required": ["alert_level", "summary", "risks", "recommended_action"],
    "additionalProperties": False,
}
```

- [ ] **Step 2: Apply the schema and lower output budget**

Change `DEFAULT_NUM_PREDICT` to `96`, add `"format": _OUTPUT_SCHEMA` to the request, and keep all existing options and environment overrides.

- [ ] **Step 3: Aggregate detector context**

Group detections in first-seen label order and render count plus maximum confidence only.

```python
grouped: dict[str, list[Detection]] = {}
for detection in detections:
    grouped.setdefault(detection.label, []).append(detection)
return "\n".join(
    f"- {label}: count={len(items)}, max_conf={max(item.confidence for item in items):.2f}"
    for label, items in grouped.items()
) or "- none"
```

- [ ] **Step 4: Preserve the public response contract**

In `_parse`, derive observations only when the compact response omits them.

```python
summary = str(data.get("summary", ""))
raw_observations = data.get("observations")
observations = (
    cls._normalize_strings(raw_observations)
    if raw_observations is not None
    else ([summary] if summary else [])
)
```

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest tests/test_vlm_context.py tests/test_model_config.py -v`

Expected: all focused tests pass.

- [ ] **Step 6: Commit the request optimization**

```powershell
git add src/camera_ai/vlm/ollama_qwen.py tests/test_vlm_context.py
git commit -m "perf: compact qwen vision request"
```

---

### Task 3: Update configuration documentation and verify regression safety

**Files:**
- Modify: `README.md`
- Modify: `docs/camera-ai-pipeline.md`

**Interfaces:**
- Consumes: final request defaults and response behavior from Task 2
- Produces: accurate operator documentation

- [ ] **Step 1: Update documented defaults and request behavior**

Change documented `OLLAMA_NUM_PREDICT` from `160` to `96`. State that Qwen receives two separate images, compact label/count/max-confidence YOLO context, and returns the four-field JSON schema.

- [ ] **Step 2: Run the complete test suite**

Run: `python -m pytest tests -v`

Expected: all tests pass with no Ollama or YOLO service required.

- [ ] **Step 3: Check formatting and worktree scope**

Run: `git diff --check` and `git status --short`

Expected: no whitespace errors; the existing untracked `yolo26n.pt` remains untouched.

- [ ] **Step 4: Commit documentation**

```powershell
git add README.md docs/camera-ai-pipeline.md
git commit -m "docs: describe compact qwen output"
```

- [ ] **Step 5: Measure one real warm request manually**

Restart the API, submit the same video twice within the keep-alive period, and compare the second request's `[ollama] prompt_ms`, `output_ms`, token counts and pipeline `qwen_ms` against the previous 6–7 second warm result. Do not encode a timing threshold in pytest.

