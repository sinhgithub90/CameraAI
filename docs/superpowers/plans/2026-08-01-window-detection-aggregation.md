# Window Detection Aggregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Use frame-level detection statistics for event routing and remove raw bounding boxes from per-video benchmark JSON.

**Architecture:** Add a focused aggregation module that derives peak counts and same-frame proximity from `VideoFrameObservation` values. Store the aggregate on `VideoWindowObservation`, make the router consume it, and summarize labels/confidence only at the benchmark serialization boundary.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, existing OpenCV video window pipeline.

## Global Constraints

- Keep raw bbox internally and preserve the existing API `detections` field.
- Do not add tracking or assign unique object identities.
- Count proximity only between detections from the same frame.
- Benchmark JSON must not contain raw bbox arrays.
- Keep current candidate types and conservative VLM call policy.

---

### Task 1: Frame-level detection aggregate

**Files:**
- Create: `src/camera_ai/detection_aggregation.py`
- Modify: `src/camera_ai/schemas.py`
- Create: `tests/test_detection_aggregation.py`

**Interfaces:**
- Consumes: `Sequence[VideoFrameObservation]`.
- Produces: `WindowDetectionAggregate` and `aggregate_window_detections(observations)`.

- [ ] **Step 1: Write failing tests for peak counts and same-frame proximity**

```python
def test_repeated_person_across_frames_has_peak_count_one():
    result = aggregate_window_detections([frame(person()), frame(person())])
    assert result.person_peak_count == 1
    assert result.person_detection_frames == 2


def test_proximity_only_counts_pairs_in_the_same_frame():
    separate = aggregate_window_detections([frame(person()), frame(car())])
    together = aggregate_window_detections([frame(person(), car())])
    assert separate.proximity_frame_count == 0
    assert together.proximity_frame_count == 1
```

- [ ] **Step 2: Verify RED**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_detection_aggregation.py -q`

Expected: collection fails because `camera_ai.detection_aggregation` does not exist.

- [ ] **Step 3: Implement the aggregate model and pure function**

Add `WindowDetectionAggregate` to schemas with integer fields
`person_peak_count`, `vehicle_peak_count`, `person_detection_frames`,
`vehicle_detection_frames`, `proximity_frame_count`, and
`sampled_frame_count`, all defaulting to zero. Implement the function with one
pass over frames and reuse the router's bbox-nearness calculation through a
public `detections_are_near(left, right)` helper.

- [ ] **Step 4: Verify GREEN**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_detection_aggregation.py -q`

Expected: all aggregation tests pass.

### Task 2: Route with aggregates instead of accumulated detections

**Files:**
- Modify: `src/camera_ai/schemas.py`
- Modify: `src/camera_ai/router.py`
- Modify: `src/camera_ai/video_windows.py`
- Modify: `tests/test_event_router.py`
- Modify: `tests/test_pipeline_events.py`

**Interfaces:**
- Consumes: `WindowDetectionAggregate` from Task 1.
- Produces: `VideoWindowObservation.detection_aggregate` and candidate evidence with peak/frame counters.

- [ ] **Step 1: Write failing router and processor tests**

```python
assert candidate.evidence == {
    "person_peak_count": 1,
    "vehicle_peak_count": 1,
    "person_detection_frames": 2,
    "vehicle_detection_frames": 2,
    "proximity_frame_count": 2,
    "sampled_frame_count": 2,
    "motion_peak": 0.8,
}
```

Add a processor test where one person repeats in two frames and assert the
candidate evidence reports `person_peak_count == 1`, not 2.

- [ ] **Step 2: Verify RED**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_event_router.py tests/test_pipeline_events.py -q`

Expected: evidence lacks the new peak/frame fields.

- [ ] **Step 3: Wire aggregation before routing**

Add `detection_aggregate: WindowDetectionAggregate` to
`VideoWindowObservation`. In `VideoWindowProcessor.process()`, call
`aggregate_window_detections(window.observations)` after detections are
collected and pass it into `routing_observation`. Update `route_observation()`
to use peak counts and `proximity_frame_count > 0`; retain raw detections only
as internal evidence and compatibility data.

- [ ] **Step 4: Verify GREEN**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_detection_aggregation.py tests/test_event_router.py tests/test_pipeline_events.py -q`

Expected: all focused routing tests pass.

### Task 3: Compact benchmark detection output

**Files:**
- Modify: `scripts/benchmark_pipeline.py`
- Modify: `tests/test_benchmark_cli.py`
- Modify: `docs/benchmarks/README.md`

**Interfaces:**
- Consumes: existing API payload `window.detections`.
- Produces: `detection_summary` keyed by label, without `detections` or `bbox` in per-video window JSON.

- [ ] **Step 1: Write a failing serializer test**

```python
payload["windows"][0]["detections"] = [
    {"label": "person", "confidence": 0.7, "bbox": [0, 0, 10, 20]},
    {"label": "person", "confidence": 0.9, "bbox": [1, 0, 11, 20]},
    {"label": "car", "confidence": 0.8, "bbox": [20, 0, 40, 20]},
]
window = build_video_report(path, "analysis", payload)["windows"][0]
assert "detections" not in window
assert window["detection_summary"] == {
    "car": {"detection_count": 1, "max_confidence": 0.8},
    "person": {"detection_count": 2, "max_confidence": 0.9},
}
assert "bbox" not in json.dumps(window)
```

- [ ] **Step 2: Verify RED**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_benchmark_cli.py -q`

Expected: report still contains `detections` and lacks `detection_summary`.

- [ ] **Step 3: Implement label summary at serialization boundary**

Add `summarize_detections(detections)` that sorts labels and returns literal
`detection_count` plus maximum confidence. Replace the per-window `detections`
field in `build_video_report()` with `detection_summary`. Do not modify the API
payload or `AnalysisStore`.

- [ ] **Step 4: Verify focused and full suites**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_benchmark_cli.py tests/test_detection_aggregation.py tests/test_event_router.py tests/test_pipeline_events.py -q`

Then run: `$env:PYTHONPATH='src'; python -m pytest -q`

Expected: focused tests and full regression pass; only existing FastAPI
`on_event` deprecation warnings may remain.

- [ ] **Step 5: Check patch hygiene**

Run: `git diff --check`

Expected: no whitespace errors; preserve the unrelated untracked
`--input-file` artifact.
