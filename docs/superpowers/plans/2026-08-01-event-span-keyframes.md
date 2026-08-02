# Event-Span Keyframes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fixed before/after peak offsets with two frames surrounding a smoothed, contiguous activity span while preserving two images and one Qwen call.

**Architecture:** Keep the existing raw motion/detection change score. Add focused internal helpers for centered smoothing and span expansion, then have only the `max_keyframes == 2` branch select context around the computed event start/end. The public selector contract and every inference boundary remain unchanged.

**Tech Stack:** Python 3.12, pytest, existing Pydantic `VideoFrameObservation` models.

## Global Constraints

- Preserve `_observation_change_score` unchanged.
- Use centered three-sample mean smoothing with available samples at the edges.
- Use an active threshold of exactly `0.30 * peak_smoothed_score`.
- Bridge at most one consecutive inactive sample and stop at two.
- Select context `0.6` seconds before event start and `0.6` seconds after event end.
- Keep no more than two images and exactly one Qwen call per routed window.
- Do not add decoding, YOLO inference, dependencies, configuration, cross-window state, or benchmark writes.
- Preserve behavior when `max_keyframes != 2`.

---

### Task 1: Smooth Activity Scores and Find the Event Span

**Files:**
- Modify: `src/camera_ai/video_selection.py`
- Test: `tests/test_video_pipeline.py`

**Interfaces:**
- Consumes: raw `Sequence[float]` values returned by `_observation_change_score`.
- Produces: `_smooth_activity_scores(scores: Sequence[float]) -> list[float]` and `_event_span(scores: Sequence[float]) -> tuple[int, int]`.

- [ ] **Step 1: Add failing smoothing tests**

Import the two new internal helpers and add literal, hand-derived assertions:

```python
from camera_ai.video_selection import (
    _event_span,
    _smooth_activity_scores,
    select_keyframes,
)


def test_activity_smoothing_uses_centered_three_sample_mean():
    assert _smooth_activity_scores([0.0, 3.0, 0.0]) == [1.5, 1.0, 1.5]


def test_activity_smoothing_preserves_empty_input():
    assert _smooth_activity_scores([]) == []
```

Mutation caught: fixed three-item divisors at the edges would return `1.0` instead of `1.5`.

- [ ] **Step 2: Add failing span expansion tests**

Use scores where the threshold is unambiguous (`30%` of peak `1.0`):

```python
def test_event_span_bridges_one_inactive_sample():
    assert _event_span([1.0, 0.2, 1.0]) == (0, 2)


def test_event_span_stops_before_two_inactive_samples():
    assert _event_span([1.0, 0.2, 0.2, 1.0]) == (0, 0)


def test_event_span_uses_earliest_peak_on_tie():
    assert _event_span([0.2, 1.0, 0.2, 0.2, 1.0]) == (1, 1)
```

The tie case stops before the later peak because two consecutive inactive values separate it from the earliest peak.

- [ ] **Step 3: Run RED for missing helpers**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/test_video_pipeline.py -k "activity_smoothing or event_span" -q
```

Expected: collection fails because `_smooth_activity_scores` and `_event_span` do not exist.

- [ ] **Step 4: Implement centered smoothing**

Add constants beside `VEHICLE_LABELS`:

```python
ACTIVE_THRESHOLD_RATIO = 0.30
EVENT_CONTEXT_SECONDS = 0.6
MAX_INACTIVE_GAP = 1
```

Replace the obsolete fixed-offset constants. Add:

```python
def _smooth_activity_scores(scores: Sequence[float]) -> list[float]:
    return [
        sum(scores[max(0, index - 1) : min(len(scores), index + 2)])
        / len(scores[max(0, index - 1) : min(len(scores), index + 2)])
        for index in range(len(scores))
    ]
```

- [ ] **Step 5: Implement deterministic span expansion**

Add a directional helper and event-span function:

```python
def _expand_active_boundary(
    scores: Sequence[float],
    peak_position: int,
    *,
    step: int,
    threshold: float,
) -> int:
    boundary = peak_position
    inactive_run = 0
    position = peak_position + step
    while 0 <= position < len(scores):
        if scores[position] >= threshold:
            boundary = position
            inactive_run = 0
        else:
            inactive_run += 1
            if inactive_run > MAX_INACTIVE_GAP:
                break
        position += step
    return boundary


def _event_span(scores: Sequence[float]) -> tuple[int, int]:
    peak_position = max(range(len(scores)), key=lambda index: scores[index])
    threshold = scores[peak_position] * ACTIVE_THRESHOLD_RATIO
    return (
        _expand_active_boundary(
            scores, peak_position, step=-1, threshold=threshold
        ),
        _expand_active_boundary(
            scores, peak_position, step=1, threshold=threshold
        ),
    )
```

`_event_span` is called only with a non-empty, positive signal after the no-activity guard in Task 2.

- [ ] **Step 6: Run helper tests GREEN**

Run the Step 3 command again. Expected: all selected tests pass.

### Task 2: Select Frames Around the Event Span

**Files:**
- Modify: `src/camera_ai/video_selection.py`
- Test: `tests/test_video_pipeline.py`
- Modify: `README.md`
- Modify: `docs/camera-ai-pipeline.md`

**Interfaces:**
- Consumes: `_smooth_activity_scores`, `_event_span`, `_nearest_by_timestamp`, and existing raw change scores.
- Produces: unchanged `select_keyframes(observations: Sequence[VideoFrameObservation], max_keyframes: int = 8) -> list[VideoFrameObservation]` with event-span behavior for exactly two frames.

- [ ] **Step 1: Add a test helper for literal motion scores**

In `tests/test_video_pipeline.py`, add:

```python
def make_scored_observations(scores: list[float]) -> list[VideoFrameObservation]:
    return [
        VideoFrameObservation(
            frame_index=index,
            timestamp_seconds=index / 5,
            motion=MotionResult(
                motion=score > 0,
                changed_ratio=score,
                score=score,
            ),
        )
        for index, score in enumerate(scores)
    ]
```

This fixture makes raw change scores equal to the literal motion scores because detections are empty.

- [ ] **Step 2: Replace fixed-peak expectations with a failing event-span assertion**

Add:

```python
def test_two_keyframes_surround_complete_activity_span():
    observations = make_scored_observations(
        [0, 0, 0, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0]
    )

    selected = select_keyframes(observations, max_keyframes=2)

    assert [item.frame_index for item in selected] == [1, 11]
```

Hand derivation: smoothing makes indices `4..8` active; the nearest contexts to `0.8-0.6=0.2s` and `1.6+0.6=2.2s` are indices `1` and `11`. Mutation caught: fixed peak selection returns a narrower pair.

Retain the existing no-change, first/last boundary, one/two observation, detection-only, chronological, and non-two-keyframe tests. Update any fixed-offset expected indices that conflict with the event-span contract using hand-derived smoothed scores.

- [ ] **Step 3: Run RED for public selector behavior**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/test_video_pipeline.py -k keyframe -q
```

Expected: `test_two_keyframes_surround_complete_activity_span` fails because the current selector surrounds one peak.

- [ ] **Step 4: Replace only the two-frame fixed-offset branch**

After computing `change_scores` and retaining the current all-zero fallback, use:

```python
smoothed_scores = _smooth_activity_scores(change_scores)
event_start_position, event_end_position = _event_span(smoothed_scores)
event_start = observations[event_start_position]
event_end = observations[event_end_position]

before_candidates = observations[:event_start_position]
after_candidates = observations[event_end_position + 1 :]
before_frame = (
    _nearest_by_timestamp(
        before_candidates,
        event_start.timestamp_seconds - EVENT_CONTEXT_SECONDS,
    )
    if before_candidates
    else observations[0]
)
after_frame = (
    _nearest_by_timestamp(
        after_candidates,
        event_end.timestamp_seconds + EVENT_CONTEXT_SECONDS,
    )
    if after_candidates
    else observations[-1]
)
return [before_frame, after_frame]
```

Remove `BEFORE_PEAK_SECONDS`, `AFTER_PEAK_SECONDS`, and direct peak-frame context selection. Do not change `_observation_change_score` or the general selector below this branch.

- [ ] **Step 5: Run selector and integration tests GREEN**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/test_video_pipeline.py tests/test_pipeline_events.py -q
```

Expected: all tests pass and integration continues to use one VLM call with at most two frames.

- [ ] **Step 6: Update current documentation**

Replace fixed peak-offset wording in `README.md` and `docs/camera-ai-pipeline.md` with the exact event-span policy:

```text
The selector smooths change scores across three observations, expands an
activity span at 30% of the smoothed peak while tolerating one inactive sample,
then selects context 0.6 seconds before the span and 0.6 seconds after it.
```

State that selection stays within one five-second window and adds no inference.

- [ ] **Step 7: Run focused and full verification**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/test_video_pipeline.py tests/test_pipeline_events.py tests/test_benchmark_cli.py -q
python -m pytest -q
git diff --check
```

Expected: focused and full suites pass; only existing FastAPI deprecation warnings remain.

- [ ] **Step 8: Verify scope and latency invariants**

Run:

```powershell
git diff --stat
git diff -- src/camera_ai/video_selection.py tests/test_video_pipeline.py README.md docs/camera-ai-pipeline.md
rg -n "analyze_with_trace|analyze_sequence" src/camera_ai/video_windows.py
git status --short
```

Confirm only the four planned files changed, `_observation_change_score` is byte-for-byte unchanged in the diff, and no VLM call path changed.

- [ ] **Step 9: Commit implementation**

```powershell
git add -- src/camera_ai/video_selection.py tests/test_video_pipeline.py README.md docs/camera-ai-pipeline.md
git commit -m "feat: select keyframes around event activity span"
```

Do not stage files under `runs/` or temporary visual-review images.
