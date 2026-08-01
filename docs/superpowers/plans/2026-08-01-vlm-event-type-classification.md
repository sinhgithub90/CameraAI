# VLM Event Type Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let candidate-aware Qwen responses classify a stable event taxonomy through `decision`, `event_type`, and a concise summary.

**Architecture:** Define the taxonomy beside the Ollama candidate schema, validate both membership and decision/event consistency before marking output valid, and propagate the validated VLM event type through the existing trace and `ModelDecision` contracts. Invalid output falls back to `uncertain + unknown_event` without using the router candidate as a conclusion.

**Tech Stack:** Python 3.11+, Ollama JSON schema, Pydantic v2, pytest.

## Global Constraints

- Candidate output contains exactly `decision`, `event_type`, and `summary`.
- Event type belongs to the approved fixed taxonomy.
- `no` pairs only with `no_event`; `uncertain` only with `unknown_event`; `yes` only with a concrete event.
- Summary is 1–2 Vietnamese sentences, approximately 40 words or fewer.
- Invalid output becomes low/degraded `uncertain + unknown_event` and creates no alert.
- Keep `OLLAMA_NUM_PREDICT=128` and keep the non-candidate schema unchanged.

---

### Task 1: Taxonomy schema and consistency validation

**Files:**
- Modify: `src/camera_ai/vlm/ollama_qwen.py`
- Modify: `tests/test_candidate_vlm_trace.py`

**Interfaces:**
- Consumes: candidate-aware Qwen JSON.
- Produces: a trace with VLM-selected `event_type`, or deterministic `unknown_event` fallback.

- [ ] **Step 1: Write failing valid-output tests**

Use `{"decision":"yes","event_type":"traffic_accident","summary":"Có va chạm."}` and assert schema required/properties are exactly the three fields, the schema event enum equals the taxonomy, and `trace.event_type == "traffic_accident"`. Add valid `no + no_event` and `uncertain + unknown_event` cases.

- [ ] **Step 2: Write failing inconsistency tests**

Parametrize `no + traffic_accident`, `yes + no_event`, `uncertain + person_fall`, and an out-of-taxonomy type. Assert each returns `raw_output_valid=False`, `decision="uncertain"`, `event_type="unknown_event"`, low alert level, and degraded scene.

- [ ] **Step 3: Verify RED**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_candidate_vlm_trace.py -q`

Expected: current two-field schema rejects event type and trace still copies candidate type.

- [ ] **Step 4: Implement schema and validator**

Add the eight-value taxonomy tuple, expose it as the JSON schema enum, require all three fields, and add a pure `_candidate_output_is_consistent(decision, event_type)` helper. Candidate validity requires schema fields, non-empty summary, membership, and consistent pairing. Use parsed event type only when valid; otherwise use `unknown_event`. Update prompt with the taxonomy and the no/uncertain/yes pairing rules, plus 1–2 sentences up to roughly 40 words.

- [ ] **Step 5: Verify GREEN**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_candidate_vlm_trace.py -q`

Expected: valid and invalid taxonomy tests pass.

### Task 2: Domain propagation, documentation, and regression

**Files:**
- Modify: `tests/test_pipeline_events.py`
- Modify: `README.md`
- Modify: `docs/camera-ai-pipeline.md`

**Interfaces:**
- Consumes: `VLMAnalysisTrace.event_type` from Task 1.
- Produces: `ModelDecision.event_type` and benchmark metadata matching the Qwen classification.

- [ ] **Step 1: Add a failing pipeline propagation assertion**

Change the trace fake to return `event_type="traffic_accident"` and assert:

```python
assert result.decision.event_type == "traffic_accident"
assert result.alert_event.event_type == "traffic_accident"
```

- [ ] **Step 2: Verify RED or confirm existing propagation**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_pipeline_events.py -q`

Expected: if propagation already exists the test passes immediately as a characterization; no production change is needed because `decision_from_trace()` already prefers `trace.event_type`.

- [ ] **Step 3: Document the three-field response**

Document the taxonomy and state that candidate is guidance while VLM event type is the final classification only for valid JSON. Document `unknown_event` fallback and retain the 128-token default.

- [ ] **Step 4: Run focused and full verification**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_candidate_vlm_trace.py tests/test_pipeline_events.py tests/test_event_contracts.py -q`

Then run: `$env:PYTHONPATH='src'; python -m pytest -q`

Expected: focused and full suites pass; only existing FastAPI `on_event` warnings may remain.

- [ ] **Step 5: Check patch hygiene**

Run: `git diff --check`

Expected: no whitespace errors and the unrelated `--input-file` artifact remains untracked.
