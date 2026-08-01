# Compact Benchmark JSON Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a compact per-video benchmark JSON that reports confirmed event severity correctly, omits detection summaries and verbose metadata, and preserves detailed pipeline timing.

**Architecture:** Keep the processing pipeline and stored analysis payload unchanged. Change only the projection in `scripts/benchmark_pipeline.py`: resolve the output level from a confirmed alert before falling back to scene security, then flatten the routed candidate and decision into compact `qwen` and `timing` objects. Update the focused CLI tests to lock the intentional output-schema change.

**Tech Stack:** Python 3, pytest, existing dictionary-based benchmark payloads.

## Global Constraints

- Preserve one output entry for every five-second window, including green windows.
- For a confirmed alert, derive `alert_level` from `event_metadata.alert.severity`; otherwise fall back to `security.alert_level`, then `low`.
- Remove `detection_summary` and verbose candidate, decision, VLM trace, and routing objects from the per-window JSON.
- Keep `motion_ms`, `detector_ms`, `keyframe_ms`, `qwen_ms`, `total_ms`, `queue_wait_ms`, `wall_clock_ms`, and the five-second budget result.
- Keep the top-level `video`, `camera_id`, `analysis_id`, `status`, `level_summary`, `performance_summary`, and `windows` fields.

---

### Task 1: Project confirmed severity into compact window reports

**Files:**
- Modify: `tests/test_benchmark_cli.py`
- Modify: `scripts/benchmark_pipeline.py:91-164`

**Interfaces:**
- Consumes: existing analysis payload fields `security`, `event_metadata.alert`, `event_metadata.candidates`, `event_metadata.decision`, `event_metadata.vlm_call`, `vlm.summary`, `qwen_input.timestamps_seconds`, and `timing`.
- Produces: `build_video_report(video_path: str | Path, analysis_id: str, payload: dict) -> dict` with compact `qwen` and `timing` objects.

- [ ] **Step 1: Expand the test payload fixture with the compact report inputs**

Update `analysis_payload` so each window includes Qwen timestamps and all timing measurements:

```python
"qwen_input": {
    "frame_count": 2,
    "timestamps_seconds": [index * 5 + 0.8, index * 5 + 4.8],
},
"timing": {
    "motion_ms": 10.0,
    "detector_ms": 20.0,
    "keyframe_ms": 0.1,
    "qwen_ms": 4000.0,
    "total_ms": 4030.1,
    "queue_wait_ms": 25.0,
    "wall_clock_ms": 4055.1,
},
```

- [ ] **Step 2: Write failing tests for severity precedence and compact output**

Replace the verbose-metadata test with a confirmed-accident test:

```python
def test_video_report_uses_confirmed_alert_severity_and_compact_qwen_output(tmp_path):
    payload = analysis_payload("low")
    payload["windows"][0]["event_metadata"] = {
        "candidates": [
            {
                "candidate_id": "candidate-1",
                "candidate_type": "vehicle_scene",
                "priority": "low",
                "evidence": {"vehicle_peak_count": 4},
            }
        ],
        "decision": {
            "candidate_id": "candidate-1",
            "model": "qwen",
            "decision": "yes",
            "event_type": "traffic_accident",
            "evidence": [],
            "raw_output_valid": True,
        },
        "alert": {"severity": "high", "event_type": "traffic_accident"},
        "vlm_call": {"call_vlm": True, "candidate_id": "candidate-1"},
    }

    report = build_video_report(tmp_path / "event.mp4", "analysis-3", payload)

    assert report["level_summary"] == {
        "green": 0,
        "orange": 0,
        "red": 1,
        "highest_level": "high",
    }
    assert report["windows"][0]["alert_level"] == "high"
    assert report["windows"][0]["candidate_type"] == "vehicle_scene"
    assert report["windows"][0]["qwen"] == {
        "called": True,
        "decision": "yes",
        "event_type": "traffic_accident",
        "summary": "window 0",
        "timestamps_seconds": [0.8, 4.8],
    }
```

Replace the detection-summary test with an exact schema/timing test:

```python
def test_video_report_omits_detection_summary_and_keeps_detailed_timing(tmp_path):
    payload = analysis_payload("low")
    payload["windows"][0]["detections"] = [
        {"label": "person", "confidence": 0.9, "bbox": [0, 0, 10, 20]}
    ]

    window = build_video_report(
        tmp_path / "objects.mp4", "analysis-5", payload
    )["windows"][0]

    assert set(window) == {
        "window_index",
        "start_seconds",
        "end_seconds",
        "alert_level",
        "candidate_type",
        "qwen",
        "timing",
    }
    assert window["timing"] == {
        "motion_ms": 10.0,
        "detector_ms": 20.0,
        "keyframe_ms": 0.1,
        "qwen_ms": 4000.0,
        "total_ms": 4030.1,
        "queue_wait_ms": 25.0,
        "wall_clock_ms": 4055.1,
        "within_budget": True,
    }
    assert "detection_summary" not in window
    assert "bbox" not in json.dumps(window)
```

Update the budget assertions to read `window["timing"]["within_budget"]`. Add a skipped-window assertion that `qwen.called` is `False` and both `decision` and `event_type` are `None`.

- [ ] **Step 3: Run the focused tests and verify the old projection fails**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_benchmark_cli.py -q
```

Expected: failures show that confirmed severity is still ignored, verbose fields remain, and `within_budget` is still outside `timing`.

- [ ] **Step 4: Implement compact projection helpers and schema**

In `scripts/benchmark_pipeline.py`, add focused helpers above `build_video_report`:

```python
_ALERT_LEVELS = {"low", "medium", "high"}


def resolve_window_alert_level(window: dict) -> str:
    event_metadata = window.get("event_metadata", {})
    alert = event_metadata.get("alert") or {}
    security = window.get("security", {})
    level = alert.get("severity") or security.get("alert_level", "low")
    return level if level in _ALERT_LEVELS else "low"


def primary_candidate_type(event_metadata: dict) -> str | None:
    candidates = event_metadata.get("candidates") or []
    if not candidates:
        return None
    vlm_call = event_metadata.get("vlm_call") or {}
    decision = event_metadata.get("decision") or {}
    primary_id = vlm_call.get("candidate_id") or decision.get("candidate_id")
    if primary_id is not None:
        for candidate in candidates:
            if candidate.get("candidate_id") == primary_id:
                return candidate.get("candidate_type")
    return candidates[0].get("candidate_type")
```

Inside `build_video_report`, resolve the level with `resolve_window_alert_level(window)`, retain the existing legacy fallback for `vlm_call`, and project each window as:

```python
timing = window.get("timing", {})
decision = event_metadata.get("decision") or {}
qwen_input = window.get("qwen_input", {})
total_ms = float(timing.get("total_ms", 0.0))

windows.append(
    {
        "window_index": window.get("window_index"),
        "start_seconds": window.get("start_seconds"),
        "end_seconds": window.get("end_seconds"),
        "alert_level": level,
        "candidate_type": primary_candidate_type(event_metadata),
        "qwen": {
            "called": call_vlm,
            "decision": decision.get("decision"),
            "event_type": decision.get("event_type"),
            "summary": window.get("vlm", {}).get("summary", ""),
            "timestamps_seconds": qwen_input.get("timestamps_seconds", []),
        },
        "timing": {
            "motion_ms": float(timing.get("motion_ms", 0.0)),
            "detector_ms": float(timing.get("detector_ms", 0.0)),
            "keyframe_ms": float(timing.get("keyframe_ms", 0.0)),
            "qwen_ms": float(timing.get("qwen_ms", 0.0)),
            "total_ms": total_ms,
            "queue_wait_ms": float(timing.get("queue_wait_ms", 0.0)),
            "wall_clock_ms": float(timing.get("wall_clock_ms", 0.0)),
            "within_budget": total_ms <= PROCESSING_BUDGET_MS,
        },
    }
)
```

Delete the old per-window `summary`, `risks`, `recommended_action`, `detection_summary`, `qwen_input`, verbose `timing`, `candidates`, `decision`, `raw_output_valid`, `vlm_call`, and `within_processing_budget` projection fields. Do not change the internal processing payload.

- [ ] **Step 5: Run the focused tests and verify the compact report passes**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_benchmark_cli.py -q
```

Expected: all tests in `tests/test_benchmark_cli.py` pass.

- [ ] **Step 6: Run the complete test suite**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest -q
```

Expected: the complete suite passes with no new failures.

- [ ] **Step 7: Commit the implementation**

```powershell
git add -- scripts/benchmark_pipeline.py tests/test_benchmark_cli.py
git commit -m "feat: compact benchmark video reports"
```
