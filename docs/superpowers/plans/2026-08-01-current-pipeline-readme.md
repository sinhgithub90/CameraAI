# Current Pipeline README Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the root and benchmark README files with the implemented async video pipeline and compact benchmark JSON.

**Architecture:** Documentation-only change. Use the runtime constructor, router, keyframe selector, Qwen analyzer, severity mapping, and benchmark report builder as the sources of truth; do not change code or runtime behavior.

**Tech Stack:** Markdown, PowerShell CLI examples, Python module help, pytest.

## Global Constraints

- Document five-second windows, motion-first sampling, scene-composition routing, two event-span keyframes, one composite Qwen call, and event-type severity mapping.
- Distinguish the pipeline constructor's one-window development default from the FastAPI runtime's full-video configuration.
- Document the compact per-window `candidate_type`, `qwen`, and `timing` objects.
- State that raw boxes, `detection_summary`, candidate evidence, IDs, and verbose routing/decision metadata are excluded from per-video benchmark JSON.
- Preserve historical benchmark results as historical context rather than presenting them as current quality measurements.

---

### Task 1: Refresh current pipeline and benchmark documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/benchmarks/README.md`

**Interfaces:**
- Consumes: current behavior from `apps/api/main.py`, `src/camera_ai/pipeline.py`, `src/camera_ai/video_selection.py`, `src/camera_ai/router.py`, `src/camera_ai/vlm/ollama_qwen.py`, `src/camera_ai/event_models.py`, and `scripts/benchmark_pipeline.py`.
- Produces: accurate setup, architecture, API, CLI, JSON schema, and performance-target documentation.

- [ ] **Step 1: Rewrite the root pipeline overview**

Keep model setup and startup commands. Replace the current pipeline section with a numbered flow that explicitly covers full-video FastAPI processing, five-second windows, 5 FPS motion, 2 FPS YOLO, static skip, neutral router candidates, event-span keyframe selection, traffic/generic prompt profiles, one composite Qwen request, the three-field response, and severity mapping.

Add concise endpoint and quick benchmark CLI sections using:

```powershell
python -m scripts.benchmark_pipeline `
  --input-file "D:\CongViec\CameraAI\CameraAI\videos\RoadAccidents010_x264.mp4" `
  --output-dir "D:\CongViec\CameraAI\CameraAI\runs\alerts"
```

Show one compact JSON window containing `candidate_type`, `qwen.called`, `qwen.decision`, `qwen.event_type`, `qwen.summary`, `qwen.timestamps_seconds`, and every detailed timing field.

- [ ] **Step 2: Replace the outdated benchmark CLI contract**

In `docs/benchmarks/README.md`, retain the corpus, metrics, and historical smoke-baseline sections. Rewrite `Alert-only video CLI` and `Qwen gate and five-second processing budget` so they describe:

```json
{
  "alert_level": "high",
  "candidate_type": "vehicle_scene",
  "qwen": {
    "called": true,
    "decision": "yes",
    "event_type": "traffic_accident",
    "summary": "...",
    "timestamps_seconds": [5.8, 9.8]
  },
  "timing": {
    "motion_ms": 10.3,
    "detector_ms": 337.7,
    "keyframe_ms": 0.2,
    "qwen_ms": 3758.6,
    "total_ms": 4106.8,
    "queue_wait_ms": 46.0,
    "wall_clock_ms": 4156.0,
    "within_budget": true
  }
}
```

Explain alert severity precedence and explicitly list omitted detection/debug fields.

- [ ] **Step 3: Validate documented commands and contracts**

Run:

```powershell
python -m scripts.benchmark_pipeline --help
$env:PYTHONPATH='src'; python -m pytest tests/test_benchmark_cli.py -q
rg -n "detection_summary|raw_output_valid|within_processing_budget|exposes routed.*candidates" README.md docs/benchmarks/README.md
git diff --check
```

Expected: CLI help exits 0; benchmark CLI tests pass; any matches for removed names only state that those fields are omitted; diff check exits 0.

- [ ] **Step 4: Commit and push documentation**

```powershell
git add -- README.md docs/benchmarks/README.md
git commit -m "docs: align readmes with current video pipeline"
git push origin feature/camera-ai-module
```
