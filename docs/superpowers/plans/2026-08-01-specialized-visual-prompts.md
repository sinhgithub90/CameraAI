# Specialized Visual Prompts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let neutral router candidates select traffic or generic visual-analysis prompts without embedding candidate, evidence, or YOLO data in Qwen requests.

**Architecture:** Keep prompt profile selection local to `ollama_qwen.py` through one pure `_candidate_prompt(candidate_type)` function. Traffic candidates receive positive temporal collision criteria; all other candidates receive generic visual-event guidance. Legacy non-candidate requests keep their schema but also stop embedding detection summaries.

**Tech Stack:** Python 3.12, pytest, requests payload tests, existing Ollama JSON schemas.

## Global Constraints

- Candidate types only select a profile; no candidate value appears in prompt content.
- No router evidence, YOLO label, detection count, confidence, or bbox appears in any VLM prompt.
- Traffic profile applies only to `vehicle_scene` and `person_vehicle_scene`.
- Candidate output remains exactly `decision`, `event_type`, and `summary`.
- Legacy non-candidate output schema remains unchanged.
- Preserve one composite image, one request, `num_ctx=4096`, `num_predict=128`, and `temperature=0`.
- Preserve HTTP, parsing, degraded-output, event-consistency, and candidate-linkage behavior.
- Do not call Ollama or rewrite benchmark output during automated verification.

---

### Task 1: Candidate Prompt Profile Selection

**Files:**
- Modify: `src/camera_ai/vlm/ollama_qwen.py`
- Test: `tests/test_candidate_vlm_trace.py`

**Interfaces:**
- Consumes: `candidate.candidate_type: str` inside `OllamaQwenAnalyzer._analyze_frames_trace`.
- Produces: `_candidate_prompt(candidate_type: str) -> str`; the analyzer public API and trace contracts remain unchanged.

- [ ] **Step 1: Rewrite the generic candidate payload test to require visual-only input**

Pass data whose accidental inclusion is easy to detect:

```python
detections = [
    Detection(label="forklift_secret", confidence=0.87, bbox=[1, 2, 3, 4])
]
generic_candidate = CandidateEvent(
    candidate_id="candidate-1",
    window_id="window-1",
    candidate_type="person_scene",
    evidence={"secret_router_signal": 99},
    priority=Priority.LOW,
)
```

After capturing the outgoing prompt, assert:

```python
assert "Phân tích trực tiếp hình ảnh" in prompt
assert "person_scene" not in prompt
assert "secret_router_signal" not in prompt
assert "forklift_secret" not in prompt
assert "count=" not in prompt
assert "max_conf=" not in prompt
assert "tiếp xúc hoặc chồng lấn" not in prompt
```

Keep assertions for the three-field schema and trace result. Mutation caught: interpolating any candidate/evidence/detection field fails.

- [ ] **Step 2: Add a failing traffic-profile payload test**

Capture a request made with `candidate("vehicle_scene")` and assert:

```python
assert "So sánh trạng thái TRƯỚC và SAU trong cảnh giao thông" in prompt
assert "tách rời chuyển thành tiếp xúc hoặc chồng lấn" in prompt
assert "dừng ở vị trí tương đối bất thường" in prompt
assert "Không bắt buộc nhìn thấy đúng khoảnh khắc va chạm" in prompt
assert "Chỉ chọn person_vehicle_interaction" in prompt
assert "vehicle_scene" not in prompt
```

Repeat selection with `person_vehicle_scene` through parametrization so both traffic candidate values are protected.

- [ ] **Step 3: Run candidate prompt tests RED**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/test_candidate_vlm_trace.py -q
```

Expected: generic test fails because candidate/evidence remain in the prompt; traffic test fails because specialized guidance is missing.

- [ ] **Step 4: Define common and profile-specific prompt text**

In `ollama_qwen.py`, replace `_CANDIDATE_PROMPT` with:

```python
TRAFFIC_CANDIDATE_TYPES = frozenset({"vehicle_scene", "person_vehicle_scene"})

_CANDIDATE_PROMPT_BASE = (
    "Phân tích trực tiếp hình ảnh camera và trả về đúng JSON bằng tiếng Việt.\n"
    "- decision: đúng một trong yes | no | uncertain.\n"
    "- event_type: chọn đúng một giá trị trong: {event_types}.\n"
    "- no chỉ đi với no_event; uncertain chỉ đi với unknown_event; yes phải "
    "đi với một sự kiện cụ thể.\n"
    "- summary: bắt buộc, 1–2 câu ngắn, mục tiêu không quá 40 từ.\n"
)

_GENERIC_VISUAL_GUIDANCE = (
    "So sánh các ảnh theo thứ tự thời gian và chỉ phân loại sự kiện quan sát "
    "được. Chỉ chọn uncertain khi chất lượng ảnh hoặc che khuất không cho phép "
    "xác định thay đổi."
)

_TRAFFIC_VISUAL_GUIDANCE = (
    "So sánh trạng thái TRƯỚC và SAU trong cảnh giao thông. "
    "Chọn traffic_accident nếu phương tiện từ tách rời chuyển thành tiếp xúc "
    "hoặc chồng lấn, đổi hướng đột ngột, hoặc dừng ở vị trí tương đối bất thường. "
    "Không bắt buộc nhìn thấy đúng khoảnh khắc va chạm, hư hỏng hoặc người ngã "
    "khi chuyển tiếp trước/sau cho thấy va chạm. "
    "Chỉ chọn person_vehicle_interaction khi có tương tác người–xe nhưng không "
    "có dấu hiệu va chạm. Chỉ chọn uncertain khi chất lượng ảnh hoặc che khuất "
    "không cho phép xác định thay đổi."
)
```

- [ ] **Step 5: Implement pure profile selection**

Add:

```python
def _candidate_prompt(candidate_type: str) -> str:
    guidance = (
        _TRAFFIC_VISUAL_GUIDANCE
        if candidate_type in TRAFFIC_CANDIDATE_TYPES
        else _GENERIC_VISUAL_GUIDANCE
    )
    return (
        _CANDIDATE_PROMPT_BASE.format(event_types=", ".join(EVENT_TYPES))
        + guidance
    )
```

In `_analyze_frames_trace`, replace formatting of candidate/evidence/detections with:

```python
prompt = _candidate_prompt(candidate.candidate_type)
output_schema = _CANDIDATE_OUTPUT_SCHEMA
```

Do not change how `candidate` is passed to trace/decision code.

- [ ] **Step 6: Run candidate tests GREEN**

Run the Step 3 command again. Expected: all candidate trace tests pass.

### Task 2: Remove Detection Context from Non-Candidate Prompts

**Files:**
- Modify: `src/camera_ai/vlm/ollama_qwen.py`
- Modify: `tests/test_vlm_context.py`
- Modify: `README.md`
- Modify: `docs/camera-ai-pipeline.md`

**Interfaces:**
- Consumes: existing `_PROMPT` and `analyze/analyze_sequence` method arguments.
- Produces: legacy prompt content independent of `detections`; public method signatures and legacy output schema remain unchanged.

- [ ] **Step 1: Replace the detection-format test with a failing outgoing-payload test**

Remove `test_detection_prompt_summarizes_count_and_max_confidence_per_label`. Add a real boundary test:

```python
def test_non_candidate_prompt_does_not_embed_detection_context(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "camera_ai.vlm.ollama_qwen.requests.post",
        lambda url, **kwargs: captured.update(kwargs) or FakeResponse(),
    )
    detection = Detection(
        label="forklift_secret",
        confidence=0.87,
        bbox=[1, 2, 3, 4],
    )

    OllamaQwenAnalyzer().analyze(
        np.zeros((64, 64, 3), dtype=np.uint8),
        [detection],
    )

    prompt = captured["json"]["messages"][0]["content"]
    assert "forklift_secret" not in prompt
    assert "count=" not in prompt
    assert "max_conf=" not in prompt
    assert "Dữ liệu YOLO" not in prompt
```

Mutation caught: calling `_format_detections` or retaining `{detections}` fails.

- [ ] **Step 2: Run the payload test RED**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/test_vlm_context.py -k "non_candidate_prompt" -q
```

Expected: failure because `_PROMPT` currently contains the formatted label and YOLO heading.

- [ ] **Step 3: Remove detector text from legacy prompt construction**

Delete the `Dữ liệu YOLO:\n{detections}` suffix from `_PROMPT`. In `_analyze_frames_trace`:

```python
if candidate is not None:
    prompt = _candidate_prompt(candidate.candidate_type)
    output_schema = _CANDIDATE_OUTPUT_SCHEMA
else:
    prompt = _PROMPT
    output_schema = _OUTPUT_SCHEMA
```

Remove `det_lines = self._format_detections(detections)` and delete `_format_detections`. Keep the `detections` argument because fallback construction still consumes it on request errors.

- [ ] **Step 4: Run VLM prompt and schema tests GREEN**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/test_vlm_context.py tests/test_candidate_vlm_trace.py -q
```

Expected: all tests pass, including legacy and candidate schema assertions and composite temporal text.

- [ ] **Step 5: Update documentation**

In `README.md` and `docs/camera-ai-pipeline.md`, document:

```text
Router candidates select a traffic or generic VLM prompt profile. Candidate
values, router evidence, and YOLO summaries stay internal and are not included
in Qwen prompt text. Traffic prompts classify temporal vehicle contact and
abnormal position changes directly from the images.
```

Remove any current statement saying Qwen receives compact YOLO context.

- [ ] **Step 6: Run focused and full verification**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/test_candidate_vlm_trace.py tests/test_vlm_context.py tests/test_pipeline_events.py tests/test_benchmark_cli.py -q
python -m pytest -q
git diff --check
```

Expected: focused and full suites pass; only existing FastAPI deprecation warnings remain.

- [ ] **Step 7: Verify scope and request invariants**

Run:

```powershell
git diff --stat
git diff -- src/camera_ai/vlm/ollama_qwen.py tests/test_candidate_vlm_trace.py tests/test_vlm_context.py README.md docs/camera-ai-pipeline.md
rg -n "candidate_evidence|Dữ liệu YOLO|count=|max_conf=" src/camera_ai/vlm/ollama_qwen.py
rg -n "requests.post" src/camera_ai/vlm/ollama_qwen.py
git status --short
```

Confirm only the five planned files changed, no router/Yolo context string remains, output schemas are unchanged, and one request call remains.

- [ ] **Step 8: Commit implementation**

```powershell
git add -- src/camera_ai/vlm/ollama_qwen.py tests/test_candidate_vlm_trace.py tests/test_vlm_context.py README.md docs/camera-ai-pipeline.md
git commit -m "feat: route specialized visual prompts"
```

Do not stage files under `runs/` or temporary visual-review images.
