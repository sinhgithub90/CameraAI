# Before/After Peak Keyframes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Select two Qwen frames around the strongest change—one near one second before and one near 0.8 seconds after—without adding inference or changing the five-second window pipeline.

**Architecture:** Reuse the existing `_observation_change_score` and peak selection in `video_selection.py`. Add one deterministic nearest-timestamp helper, then replace only the `max_keyframes == 2` result selection; all other keyframe limits and downstream Qwen composition remain untouched.

**Tech Stack:** Python 3.12, pytest, existing `VideoFrameObservation` Pydantic contracts.

## Global Constraints

- Keep exactly two Qwen images whenever the window has at least two observations.
- Keep one Qwen call per routed window.
- Do not add decoding, YOLO inference, tracking, cross-window state, configuration, or dependencies.
- Preserve `_observation_change_score` unchanged.
- Preserve selection behavior when `max_keyframes != 2`.
- Use fixed offsets: before `1.0` second and after `0.8` second.
- Resolve equal timestamp-distance ties in favor of the earlier timestamp.
- Do not run the real video/Ollama benchmark as part of implementation verification.

---

### Task 1: Select Frames Before and After the Peak

**Files:**
- Modify: `src/camera_ai/video_selection.py`
- Test: `tests/test_video_pipeline.py`
- Modify: `README.md`
- Modify: `docs/camera-ai-pipeline.md`

**Interfaces:**
- Consumes: `_observation_change_score(previous: VideoFrameObservation | None, current: VideoFrameObservation) -> float` and `select_keyframes(observations: Sequence[VideoFrameObservation], max_keyframes: int = 8) -> list[VideoFrameObservation]`.
- Produces: `_nearest_by_timestamp(observations: Sequence[VideoFrameObservation], target_seconds: float) -> VideoFrameObservation` and updated two-frame behavior; public signatures remain unchanged.

- [ ] **Step 1: Replace the middle-peak expectation with a failing before/after assertion**

Change the existing middle-peak test so a peak at index 15, sampled at 5 FPS, selects index 10 near `3.0 - 1.0 = 2.0s` and index 19 near `3.0 + 0.8 = 3.8s`:

```python
def test_two_keyframes_select_before_and_after_strongest_change():
    observations = make_observations(
        25,
        motion_indices={15},
        detection_indices={15},
    )

    selected = select_keyframes(observations, max_keyframes=2)

    assert [item.frame_index for item in selected] == [10, 19]
```

Mutation caught: returning the peak as the second frame produces `[10, 15]` and fails.

- [ ] **Step 2: Add failing boundary and detection-only expectations**

Update/add these behavior tests:

```python
def test_two_keyframes_select_after_frame_when_peak_is_first():
    observations = make_observations(
        8,
        motion_indices={0},
        detection_indices={0},
    )

    selected = select_keyframes(observations, max_keyframes=2)

    assert [item.frame_index for item in selected] == [0, 4]


def test_two_keyframes_select_before_frame_when_peak_is_last():
    observations = make_observations(
        21,
        motion_indices={20},
        detection_indices={20},
    )

    selected = select_keyframes(observations, max_keyframes=2)

    assert [item.frame_index for item in selected] == [15, 20]


def test_two_keyframes_surround_detection_change_without_motion():
    observations = make_observations(20, detection_indices={12})

    selected = select_keyframes(observations, max_keyframes=2)

    assert [item.frame_index for item in selected] == [7, 16]
```

Keep the existing no-change, one-observation, ordered-unique, and non-two-keyframe tests. Add an explicit two-observation assertion if absent:

```python
def test_two_keyframes_preserve_two_observations():
    selected = select_keyframes(make_observations(2), max_keyframes=2)
    assert [item.frame_index for item in selected] == [0, 1]
```

Mutations caught: always using first/last, using the peak itself, or changing the general selector.

- [ ] **Step 3: Run RED and confirm failures are caused by peak-frame output**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/test_video_pipeline.py -k keyframe -q
```

Expected: middle-peak, first-peak, and detection-only tests fail with current selections ending at the peak; calm and general-selector tests remain green.

- [ ] **Step 4: Add deterministic nearest-timestamp selection**

In `src/camera_ai/video_selection.py`, add constants beside `VEHICLE_LABELS`:

```python
BEFORE_PEAK_SECONDS = 1.0
AFTER_PEAK_SECONDS = 0.8
```

Add a helper that requires a non-empty sequence and resolves ties by earlier timestamp:

```python
def _nearest_by_timestamp(
    observations: Sequence[VideoFrameObservation],
    target_seconds: float,
) -> VideoFrameObservation:
    return min(
        observations,
        key=lambda item: (
            abs(item.timestamp_seconds - target_seconds),
            item.timestamp_seconds,
        ),
    )
```

No public export or configuration is required.

- [ ] **Step 5: Replace only the two-frame context/peak result**

Keep change-score and no-change logic intact. After calculating `event_position` and `event_frame`, select strict temporal sides:

```python
before_candidates = observations[:event_position]
after_candidates = observations[event_position + 1 :]

before_frame = (
    _nearest_by_timestamp(
        before_candidates,
        event_frame.timestamp_seconds - BEFORE_PEAK_SECONDS,
    )
    if before_candidates
    else observations[0]
)
after_frame = (
    _nearest_by_timestamp(
        after_candidates,
        event_frame.timestamp_seconds + AFTER_PEAK_SECONDS,
    )
    if after_candidates
    else observations[-1]
)
return [before_frame, after_frame]
```

Remove the old early return for `event_position == 0` and the `separated_context` calculation. Do not modify code below the two-frame branch.

- [ ] **Step 6: Run GREEN for selector and video integration**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/test_video_pipeline.py -q
```

Expected: all tests in `test_video_pipeline.py` pass, including the test that one VLM call receives two frames.

- [ ] **Step 7: Update current documentation**

In `README.md` and `docs/camera-ai-pipeline.md`, replace the current context/strongest-change wording with:

```text
For two-keyframe windows, the strongest motion/detection change defines a peak.
The selector sends the observation nearest one second before the peak and the
observation nearest 0.8 seconds after it, falling back to window boundaries.
```

Keep documentation explicit that this adds no inference and stays within the current window.

- [ ] **Step 8: Run focused and full verification**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/test_video_pipeline.py tests/test_pipeline_events.py tests/test_benchmark_cli.py -q
python -m pytest -q
git diff --check
```

Expected: focused and full suites pass; `git diff --check` reports no whitespace errors. Existing FastAPI deprecation warnings are acceptable and unrelated.

- [ ] **Step 9: Verify scope and inference invariants**

Run:

```powershell
git diff --stat
git diff -- src/camera_ai/video_selection.py tests/test_video_pipeline.py README.md docs/camera-ai-pipeline.md
rg -n "analyze_with_trace|analyze_sequence" src/camera_ai/video_windows.py
git status --short
```

Confirm the diff touches only the four planned files, leaves `_observation_change_score` unchanged, and does not add a second VLM call or stage.

- [ ] **Step 10: Commit the implementation**

```powershell
git add -- src/camera_ai/video_selection.py tests/test_video_pipeline.py README.md docs/camera-ai-pipeline.md
git commit -m "feat: select frames before and after event peak"
```

Do not stage benchmark output under `runs/`.
