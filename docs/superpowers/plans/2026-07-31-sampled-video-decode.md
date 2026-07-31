# Sampled Video Decode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Avoid materializing video frames the Motion sampler discards.

**Architecture:** Advance every frame with OpenCV `grab()`, retrieve only Motion sample indices, and preserve all existing counters and temporal limits.

**Tech Stack:** Python 3.11, OpenCV, NumPy, pytest.

## Global Constraints

- Preserve `frames_read`, frame indices, timestamps and window boundaries.
- Do not change Motion/Yolo FPS, keyframe selection or Qwen inputs.
- Add no decoder dependency.

### Task 1: Sampled frame retrieval

**Files:**
- Modify: `tests/test_video_pipeline.py`
- Modify: `src/camera_ai/pipeline.py`

**Interfaces:** `SecurityAIPipeline._analyze_video(event) -> PipelineResult`.

- [ ] Add a fake `VideoCapture` test that raises from `read()`, counts `grab()` and `retrieve()`, and exposes a 30 FPS static stream.
- [ ] Verify RED because the current loop calls `read()`.
- [ ] Replace `read()` with `grab()` and retrieve only when `idx % motion_interval == 0`.
- [ ] Verify the test reports every successful grab in `frames_read` and only sampled retrieval calls.
- [ ] Run the complete suite, compileall and diff-check.
- [ ] Benchmark 150 1080p frames and commit with `perf: decode only sampled video frames`.
