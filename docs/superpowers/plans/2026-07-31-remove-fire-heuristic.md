# Remove Fire Heuristic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Fire heuristic from image and video runtime analysis while preserving the standalone FireDetector API.

**Architecture:** `SecurityAIPipeline` owns only one detector, YOLO26n. `VLMGate` gates image analysis from YOLO detections alone, while motion remains the video trigger that sends selected frames to Qwen even when YOLO returns no objects.

**Tech Stack:** Python 3.11, pytest, OpenCV, NumPy.

## Global Constraints

- Do not instantiate or call FireDetector from `SecurityAIPipeline`.
- Keep `camera_ai.detectors.fire.FireDetector` and package exports compatible.
- Preserve full Qwen analysis for video windows containing motion.
- Do not change public result schemas.

---

### Task 1: Remove FireDetector from runtime APIs

**Files:**
- Modify: `tests/test_pipeline.py`
- Modify: `tests/test_video_pipeline.py`
- Modify: `src/camera_ai/pipeline.py`
- Modify: `src/camera_ai/gate.py`

**Interfaces:**
- `SecurityAIPipeline(detector=..., vlm=..., gate=..., motion_detector=...)`
- `VLMGate.decide(detections: list[Detection]) -> bool`

- [ ] Add regression tests that monkeypatch `camera_ai.pipeline.FireDetector` to fail if constructed and assert image/video pipeline behavior uses only the injected YOLO detector.
- [ ] Run focused tests and verify RED because the pipeline still constructs/calls FireDetector and the gate still requires two lists.
- [ ] Remove the FireDetector import, constructor parameter, instance field and detect calls. Pass only YOLO detections to the gate and result builders.
- [ ] Simplify `VLMGate.decide` to one detection list and update its documentation.
- [ ] Update existing image/video test fixtures to remove `fire_detector` arguments and assertions.
- [ ] Run focused tests until GREEN, then run the complete suite.
- [ ] Commit with `refactor: remove fire heuristic from pipeline`.

### Task 2: Synchronize configuration and documentation

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Modify: `tests/test_model_config.py`

**Interfaces:**
- FireDetector remains directly constructible for compatibility, but has no runtime configuration in the documented default pipeline.

- [ ] Remove the runtime FireDetector default test from model configuration tests; retain no-op import compatibility through existing detector unit coverage.
- [ ] Remove `FIRE_MODEL`, Fire heuristic and dual-detector descriptions from `.env.example` and README.
- [ ] Run all tests, `compileall`, `git diff --check`, and verify `weights/best.pt` remains absent.
- [ ] Run a five-second video test and confirm the label list is composed only from YOLO26n output.
- [ ] Commit with `docs: describe motion yolo qwen pipeline`.
