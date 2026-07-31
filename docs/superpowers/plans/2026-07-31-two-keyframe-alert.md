# Two-Keyframe Alert Screening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send two temporally useful event frames to Qwen by default.

**Architecture:** Add a focused two-frame branch to `select_keyframes`; keep the general selector for larger limits. Change only the pipeline default and current documentation.

**Tech Stack:** Python 3.11, pytest, NumPy, OpenCV.

### Task 1: Event-aware two-frame selection

**Files:**
- Modify: `tests/test_video_pipeline.py`
- Modify: `src/camera_ai/video_selection.py`
- Modify: `src/camera_ai/pipeline.py`
- Modify: `README.md`

- [ ] Add a failing selector test where the meaningful frames are internal rather than boundaries.
- [ ] Add a failing integration assertion that the default pipeline sends exactly two keyframes for an active window.
- [ ] Implement primary score selection plus a one-second-separated secondary frame.
- [ ] Change `SecurityAIPipeline.max_keyframes` default from four to two and update documentation.
- [ ] Run focused tests, all tests, compileall and diff-check.
- [ ] Benchmark a warm five-second request and commit with `perf: use two qwen alert keyframes`.
