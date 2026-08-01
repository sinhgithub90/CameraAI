# Compact Candidate VLM Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make candidate-aware Qwen responses contain only `decision` and one short `summary`, with all other domain fields derived deterministically by the backend.

**Architecture:** Keep the legacy non-candidate schema unchanged. For candidate requests, use a dedicated two-field JSON schema and parser path that maps decision plus candidate priority into `SceneAnalysis` and `VLMAnalysisTrace`; existing `ModelDecision` and persistence contracts remain additive and compatible.

**Tech Stack:** Python 3.11+, Pydantic v2, Ollama `/api/chat`, pytest.

## Global Constraints

- Candidate output contains exactly `decision` and `summary`.
- Keep `decision` values `yes`, `no`, and `uncertain`.
- Derive event type from `candidate.candidate_type`.
- Candidate output has empty evidence and risks.
- Only valid `yes` can create an alert; valid `uncertain` remains low and does not create an alert.
- Keep the non-candidate output schema unchanged.
- Default `OLLAMA_NUM_PREDICT` becomes 128 and remains environment-overridable.

---

### Task 1: Compact candidate schema and backend mapping

**Files:**
- Modify: `src/camera_ai/vlm/ollama_qwen.py`
- Modify: `tests/test_candidate_vlm_trace.py`
- Modify: `tests/test_vlm_context.py`

**Interfaces:**
- Consumes: one `CandidateEvent`, prepared images, and detector summaries.
- Produces: a valid `VLMAnalysisTrace` whose scene/domain fields are derived from candidate plus `decision`.

- [ ] **Step 1: Write failing schema and mapping tests**

Use Qwen content `{"decision":"yes","summary":"Có tương tác với xe."}` and assert:

```python
schema = captured["json"]["format"]
assert set(schema["required"]) == {"decision", "summary"}
assert set(schema["properties"]) == {"decision", "summary"}
assert trace.decision == "yes"
assert trace.event_type == "person_only_activity"
assert trace.evidence == []
assert trace.scene.risks == []
assert trace.scene.alert_level.value == "low"
```

Add medium-priority `yes`, valid `no`, valid `uncertain`, and truncated JSON
cases. Assert medium `yes` maps to medium, while no/uncertain/invalid map to low;
invalid remains degraded and valid uncertain does not.

- [ ] **Step 2: Verify RED**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_candidate_vlm_trace.py -q`

Expected: compact JSON is rejected because the current schema requires seven fields.

- [ ] **Step 3: Implement candidate-specific parsing**

Replace `_CANDIDATE_OUTPUT_SCHEMA` with exactly:

```python
{
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": ["yes", "no", "uncertain"]},
        "summary": {"type": "string", "minLength": 1},
    },
    "required": ["decision", "summary"],
    "additionalProperties": False,
}
```

When candidate is present, build the scene directly from parsed compact data.
Map alert level from decision and priority, return empty risks/evidence, derive
event type from the candidate, and map action to `Kiểm tra sự kiện trên camera.`,
`Tiếp tục giám sát.`, or `Kiểm tra lại hình ảnh.`. Invalid content returns a
low degraded scene and uncertain decision. Keep `_parse()` for non-candidate
requests unchanged.

- [ ] **Step 4: Verify GREEN**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_candidate_vlm_trace.py tests/test_vlm_context.py -q`

Expected: candidate and legacy schema tests pass.

### Task 2: Reduce token default, update configuration, and regress

**Files:**
- Modify: `src/camera_ai/vlm/ollama_qwen.py`
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `docs/camera-ai-pipeline.md`
- Modify: `tests/test_model_config.py`
- Modify: `tests/test_vlm_context.py`

**Interfaces:**
- Consumes: optional `OLLAMA_NUM_PREDICT` environment variable.
- Produces: default `num_predict=128`; explicit environment values still win.

- [ ] **Step 1: Change tests to require the new default**

```python
assert OllamaQwenAnalyzer().num_predict == 128
assert captured["json"]["options"]["num_predict"] == 128
```

- [ ] **Step 2: Verify RED**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_model_config.py tests/test_vlm_context.py -q`

Expected: assertions receive 256 instead of 128.

- [ ] **Step 3: Change runtime and documented defaults**

Set `DEFAULT_NUM_PREDICT = 128`, `OLLAMA_NUM_PREDICT=128` in `.env.example`, and
replace documented default 256 values with 128. Do not change explicit override
examples such as 96.

- [ ] **Step 4: Run focused and full verification**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_candidate_vlm_trace.py tests/test_model_config.py tests/test_vlm_context.py tests/test_pipeline_events.py -q`

Then run: `$env:PYTHONPATH='src'; python -m pytest -q`

Expected: focused and full suites pass; only existing FastAPI `on_event`
deprecation warnings may remain.

- [ ] **Step 5: Check patch hygiene**

Run: `git diff --check`

Expected: no whitespace errors; preserve unrelated existing changes and the
untracked `--input-file` artifact.
