# Alert-only Video Benchmark CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add single-video and directory batch inputs that always write one JSON per video, including all low/medium/high windows or failure details.

**Architecture:** Extend the existing HTTP async benchmark client without changing the API. Keep alert filtering and JSON projection as pure functions, then add a sequential batch coordinator and mutually exclusive CLI inputs while preserving manifest mode.

**Tech Stack:** Python 3.11+, argparse, requests, pathlib, Pydantic, pytest.

## Global Constraints

- Do not start the FastAPI server or run real videos during implementation.
- Process directory videos sequentially in stable filename order.
- Always write one JSON per attempted video, including low-only and failed videos.
- Write atomically and never embed frame/base64/video bytes.
- Preserve existing manifest benchmark behavior.

---

### Task 1: Add alert-only projection and output

**Files:**
- Modify: `scripts/benchmark_pipeline.py`
- Modify: `tests/test_benchmark_cli.py`

**Interfaces:**
- Consumes: completed `/analyses/{id}` payloads.
- Produces: `build_alert_report(video_path, analysis_id, payload) -> dict | None` and `write_alert_report(output_dir, report) -> Path`.

- [x] **Step 1:** Add failing tests proving low-only payloads return `None` and mixed payloads keep only medium/high windows with orange/red counts.
- [x] **Step 2:** Run the focused tests and confirm the missing functions fail.
- [x] **Step 3:** Implement minimal projection and atomic JSON writing.
- [x] **Step 4:** Run focused tests and confirm they pass.

### Task 2: Add single-file and directory modes

**Files:**
- Modify: `scripts/benchmark_pipeline.py`
- Modify: `tests/test_benchmark_cli.py`

**Interfaces:**
- Consumes: `--input-file` or `--input-dir`, output directory, existing async HTTP client.
- Produces: sequential `run_video_inputs(...)` execution and optional alert JSON per video.

- [x] **Step 1:** Add failing tests for one-file execution, stable extension-filtered directory ordering, and continue-on-error behavior.
- [x] **Step 2:** Run focused tests and verify the missing coordinator fails.
- [x] **Step 3:** Implement the coordinator, expose the completed analysis payload from the HTTP client, and add mutually exclusive argparse inputs while preserving `--manifest`.
- [x] **Step 4:** Run CLI tests and the full regression suite.
- [ ] **Step 5:** Commit the CLI, tests, and plan.
