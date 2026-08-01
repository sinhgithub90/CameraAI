# Candidate-aware VLM Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce short, candidate-aware, traceable Qwen JSON with safe invalid-output handling and benchmark visibility.

**Architecture:** Route observations before VLM, pass one primary candidate through an additive trace-capable analyzer API, and carry typed prompt/raw/validity/decision data through the existing worker/store boundaries. Preserve old analyzer methods and one-call-per-window behavior.

**Tech Stack:** Python, Pydantic, Ollama HTTP, pytest.

## Global Constraints

- Keep Qwen 4B, two keyframes, composite mode and one VLM call/window.
- Default `num_predict=256`.
- Invalid/truncated output must become uncertain and must not create an alert.
- Do not use shared mutable last-response state.

---

### Task 1: Add typed VLM trace and candidate prompt

**Files:**
- Modify: `src/camera_ai/vlm/base.py`
- Modify: `src/camera_ai/vlm/ollama_qwen.py`
- Modify: `tests/test_model_config.py`
- Modify: `tests/test_vlm_context.py`

- [x] Write failing tests for 256 tokens, candidate prompt, valid trace, and truncated-invalid trace.
- [x] Implement additive trace API and compact schema/parser.
- [x] Run focused VLM tests.

### Task 2: Route before VLM and persist trace

**Files:**
- Modify: `src/camera_ai/video_windows.py`
- Modify: `src/camera_ai/event_models.py`
- Modify: `src/camera_ai/schemas.py`
- Modify: `src/camera_ai/analysis_store.py`
- Modify: `tests/test_pipeline_events.py`
- Modify: `tests/test_artifacts.py`

- [x] Write failing processor/artifact tests for exact prompt/raw/validity.
- [x] Reorder observation/router before VLM and create decision from trace.
- [x] Persist metadata and verify invalid output creates no alert.

### Task 3: Expose trace in per-video benchmark JSON

**Files:**
- Modify: `scripts/benchmark_pipeline.py`
- Modify: `tests/test_benchmark_cli.py`
- Modify: `docs/benchmarks/README.md`

- [x] Write failing projection test for candidate/decision/raw validity.
- [x] Add fields additively to every window JSON.
- [x] Run focused and full regression tests.
