# Neutral Routing, Event Severity, and Temporal Keyframes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove event bias from router candidates, map validated Qwen event types to UI severity, and select a before/change frame pair without adding inference cost.

**Architecture:** Keep the router as the cheap Qwen gate, but emit neutral scene labels. Centralize final alert severity in `event_models.py`, based only on the validated model event taxonomy. Replace only the special two-keyframe branch with a linear temporal-change selector while preserving the general selector for other limits.

**Tech Stack:** Python 3.11+, Pydantic, pytest, NumPy/OpenCV-backed video observations.

## Global Constraints

- Do not add tracking, trajectory analysis, a detector, or another Qwen call.
- Preserve one Qwen call per candidate window and the current five-second window architecture.
- Preserve benchmark JSON structure except for neutral candidate string values and selected-frame indices.
- Keep bbox data internal and out of benchmark JSON.
- Candidate priority remains available for queue ordering and primary-candidate selection, but never determines final alert severity.
- Existing selection behavior for `max_keyframes` values other than two remains unchanged.

---

### Task 1: Neutral Router Candidate Names

**Files:**
- Modify: `src/camera_ai/router.py`
- Modify: `src/camera_ai/video_windows.py`
- Test: `tests/test_event_router.py`
- Test: `tests/test_event_contracts.py`
- Test: `tests/test_pipeline_events.py`
- Test: `tests/test_candidate_vlm_trace.py`
- Test: `tests/test_vlm_policy.py`
- Test: `tests/test_benchmark_cli.py`

**Interfaces:**
- Consumes: `route_observation(observation: VideoWindowObservation) -> list[CandidateEvent]` and the existing fire-signal candidate construction in `VideoWindowProcessor.process`.
- Produces: candidate types `person_vehicle_scene`, `multi_person_scene`, `person_scene`, `vehicle_scene`, `unexplained_motion`, and `temporally_confirmed_fire_signal`.

- [ ] **Step 1: Update router tests to require neutral names**

Replace old candidate assertions and add coverage for person-only and vehicle-only routing:

```python
assert candidates[0].candidate_type == "person_vehicle_scene"
assert [candidate.candidate_type for candidate in candidates] == ["unexplained_motion"]
assert candidates[0].candidate_type == "multi_person_scene"

def test_person_only_routes_to_neutral_scene():
    result = route_observation(observation(
        aggregate=WindowDetectionAggregate(person_peak_count=1)
    ))
    assert result[0].candidate_type == "person_scene"

def test_vehicle_only_routes_to_neutral_scene():
    result = route_observation(observation(
        aggregate=WindowDetectionAggregate(vehicle_peak_count=1)
    ))
    assert result[0].candidate_type == "vehicle_scene"
```

Update all integration and prompt assertions to the matching neutral string. Change the fire assertion to:

```python
assert any(
    candidate.candidate_type == "temporally_confirmed_fire_signal"
    for candidate in result.candidates
)
```

- [ ] **Step 2: Run focused tests and verify the old implementation fails**

Run:

```powershell
pytest tests/test_event_router.py tests/test_pipeline_events.py tests/test_candidate_vlm_trace.py tests/test_vlm_policy.py tests/test_benchmark_cli.py -q
```

Expected: failures show old candidate strings such as `possible_person_vehicle_interaction` and `person_only_activity`.

- [ ] **Step 3: Replace candidate labels without changing routing rules**

In `route_observation`, replace only the five candidate strings:

```python
"person_vehicle_scene"
"multi_person_scene"
"person_scene"
"vehicle_scene"
"unexplained_motion"
```

In the temporally confirmed fire path in `video_windows.py`, emit:

```python
fire_type = "temporally_confirmed_fire_signal"
```

Do not alter priority, evidence, proximity, routing order, or VLM policy.

- [ ] **Step 4: Run focused tests and verify they pass**

Run the same focused pytest command. Expected: PASS.

- [ ] **Step 5: Commit the neutral router change**

```powershell
git add src/camera_ai/router.py src/camera_ai/video_windows.py tests/test_event_router.py tests/test_event_contracts.py tests/test_pipeline_events.py tests/test_candidate_vlm_trace.py tests/test_vlm_policy.py tests/test_benchmark_cli.py
git commit -m "refactor: neutralize video router candidates"
```

### Task 2: Event-Type Alert Severity

**Files:**
- Modify: `src/camera_ai/event_models.py`
- Test: `tests/test_event_contracts.py`
- Test: `tests/test_pipeline_events.py`

**Interfaces:**
- Consumes: `alert_from_decision(candidate: CandidateEvent, decision: ModelDecision, *, camera_id: str, recommended_action: str = "") -> AlertEvent | None`.
- Produces: `_severity_for_event_type(event_type: str | None) -> Severity` and alert severity independent of `CandidateEvent.priority`.

- [ ] **Step 1: Add failing taxonomy severity tests**

Import `alert_from_decision` and add:

```python
@pytest.mark.parametrize(
    ("event_type", "expected"),
    [
        ("no_event", Severity.LOW),
        ("person_vehicle_interaction", Severity.LOW),
        ("unknown_event", Severity.LOW),
        ("person_fall", Severity.MEDIUM),
        ("camera_tamper", Severity.MEDIUM),
        ("traffic_accident", Severity.HIGH),
        ("fighting", Severity.HIGH),
        ("fire_smoke", Severity.HIGH),
        ("future_event", Severity.LOW),
    ],
)
def test_alert_severity_comes_from_validated_event_type(event_type, expected):
    candidate = CandidateEvent(
        candidate_id="candidate_001",
        window_id="window_001",
        candidate_type="person_vehicle_scene",
        priority=Priority.CRITICAL,
    )
    decision = ModelDecision(
        candidate_id=candidate.candidate_id,
        model="qwen",
        decision=DecisionValue.YES,
        event_type=event_type,
        raw_output_valid=True,
    )
    alert = alert_from_decision(candidate, decision, camera_id="cam_01")
    assert alert is not None
    assert alert.severity is expected
```

Keep or extend the existing parametrized test proving `no`, `uncertain`, and invalid responses create no alert.

- [ ] **Step 2: Run focused tests and verify priority-based mapping fails**

Run:

```powershell
pytest tests/test_event_contracts.py tests/test_pipeline_events.py -q
```

Expected: taxonomy cases fail because the implementation currently converts `Priority.CRITICAL` to `Severity.CRITICAL`.

- [ ] **Step 3: Implement one explicit event taxonomy mapping**

In `event_models.py`, add:

```python
_EVENT_TYPE_SEVERITY = {
    "person_fall": Severity.MEDIUM,
    "camera_tamper": Severity.MEDIUM,
    "traffic_accident": Severity.HIGH,
    "fighting": Severity.HIGH,
    "fire_smoke": Severity.HIGH,
}


def _severity_for_event_type(event_type: str | None) -> Severity:
    return _EVENT_TYPE_SEVERITY.get(event_type or "", Severity.LOW)
```

Replace the candidate-priority conversion inside `alert_from_decision` with:

```python
severity = _severity_for_event_type(decision.event_type)
```

Preserve the existing `YES` and `raw_output_valid` gate.

- [ ] **Step 4: Add a pipeline assertion that benign interaction stays green**

Use a trace VLM returning `event_type="person_vehicle_interaction"` with a medium-priority person/vehicle candidate and assert:

```python
assert result.alert_event.severity is Severity.LOW
```

Keep the existing traffic-accident trace assertion and add:

```python
assert result.alert_event.severity is Severity.HIGH
```

- [ ] **Step 5: Run focused tests and verify they pass**

Run:

```powershell
pytest tests/test_event_contracts.py tests/test_pipeline_events.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit the severity policy**

```powershell
git add src/camera_ai/event_models.py tests/test_event_contracts.py tests/test_pipeline_events.py
git commit -m "feat: map alert severity from vlm event type"
```

### Task 3: Before/Change Keyframe Pair

**Files:**
- Modify: `src/camera_ai/video_selection.py`
- Test: `tests/test_video_pipeline.py`

**Interfaces:**
- Consumes: `select_keyframes(observations: Sequence[VideoFrameObservation], max_keyframes: int = 8) -> list[VideoFrameObservation]`.
- Produces: internal `_observation_change_score(previous: VideoFrameObservation, current: VideoFrameObservation) -> float`; public signature remains unchanged.

- [ ] **Step 1: Replace the two-frame test with temporal-change cases**

Add a helper that creates explicit detections and motion, then cover:

```python
def test_two_keyframes_select_context_before_strongest_change():
    observations = make_observations(25, motion_indices={15}, detection_indices={15})
    selected = select_keyframes(observations, max_keyframes=2)
    assert [item.frame_index for item in selected] == [10, 15]

def test_two_keyframes_use_ends_when_there_is_no_change():
    selected = select_keyframes(make_observations(5), max_keyframes=2)
    assert [item.frame_index for item in selected] == [0, 4]

def test_two_keyframes_pair_first_change_with_last_frame():
    observations = make_observations(5, motion_indices={0}, detection_indices={0})
    selected = select_keyframes(observations, max_keyframes=2)
    assert [item.frame_index for item in selected] == [0, 4]

def test_two_keyframes_return_single_observation_once():
    selected = select_keyframes(make_observations(1), max_keyframes=2)
    assert [item.frame_index for item in selected] == [0]
```

Add one detection-delta test where motion is equal but a person or vehicle appears, proving label/count changes influence selection.

- [ ] **Step 2: Run keyframe tests and verify the old ranking fails**

Run:

```powershell
pytest tests/test_video_pipeline.py -k keyframe -q
```

Expected: at least the context/change assertion fails because the old branch selects two independently ranked event frames.

- [ ] **Step 3: Implement linear change scoring**

In `video_selection.py`, add helpers using detection labels already present on each observation:

```python
def _label_count(observation: VideoFrameObservation, labels: set[str]) -> int:
    return sum(item.label in labels for item in observation.detections)


def _observation_change_score(
    previous: VideoFrameObservation | None,
    current: VideoFrameObservation,
) -> float:
    previous_detections = previous.detections if previous is not None else []
    person_delta = abs(
        _label_count(current, {"person"})
        - sum(item.label == "person" for item in previous_detections)
    )
    vehicle_labels = {"bicycle", "car", "motorcycle", "bus", "truck"}
    vehicle_delta = abs(
        _label_count(current, vehicle_labels)
        - sum(item.label in vehicle_labels for item in previous_detections)
    )
    previous_labels = {item.label for item in previous_detections}
    current_labels = {item.label for item in current.detections}
    label_changed = float(previous_labels != current_labels)
    return current.motion.score + person_delta + vehicle_delta + label_changed
```

For exactly two keyframes:

1. Score the first observation against an empty baseline and each later observation against its immediate predecessor.
2. Return first/last if all change scores are zero.
3. Select the observation with the highest score; break ties by earliest position.
4. Select the latest prior observation whose timestamp is at least one second earlier.
5. Fall back to the earliest prior observation when the one-second context is unavailable.
6. If the event frame is first, pair first/last.
7. Return unique frames in chronological order.

Do not modify the existing general branch used when `max_keyframes != 2`.

- [ ] **Step 4: Run keyframe and video integration tests**

Run:

```powershell
pytest tests/test_video_pipeline.py -q
```

Expected: PASS, including one-call and two-frame integration tests.

- [ ] **Step 5: Commit the temporal selector**

```powershell
git add src/camera_ai/video_selection.py tests/test_video_pipeline.py
git commit -m "feat: select before and change keyframes"
```

### Task 4: Contract Cleanup and Full Verification

**Files:**
- Modify: `README.md`
- Modify: `docs/camera-ai-pipeline.md`
- Modify: tests containing obsolete candidate strings found by search.

**Interfaces:**
- Consumes: neutral candidate names, event-type severity policy, and temporal two-frame selection from Tasks 1–3.
- Produces: documentation and repository-wide tests consistent with the new behavior.

- [ ] **Step 1: Find and update all obsolete candidate references**

Run:

```powershell
rg -n "possible_person_vehicle_interaction|multi_person_high_motion|person_only_activity|vehicle_only_activity|unknown_motion|possible_fire_visual_change" README.md docs src tests
```

Replace behavior examples and assertions with the six neutral strings. Do not rewrite historical files under `docs/superpowers/specs` or `docs/superpowers/plans`; those record earlier designs.

- [ ] **Step 2: Document final candidate and severity responsibilities**

In `README.md` and `docs/camera-ai-pipeline.md`, state:

```text
Router candidates describe scene composition and decide whether Qwen runs.
Validated Qwen event_type determines the final alert severity.
Two Qwen frames represent context before change and the strongest change.
```

List the exact event-to-color mapping from the design spec.

- [ ] **Step 3: Run focused contract verification**

Run:

```powershell
pytest tests/test_event_router.py tests/test_event_contracts.py tests/test_pipeline_events.py tests/test_video_pipeline.py tests/test_candidate_vlm_trace.py tests/test_vlm_policy.py tests/test_benchmark_cli.py -q
```

Expected: PASS.

- [ ] **Step 4: Run full verification**

Run:

```powershell
pytest -q
git diff --check
```

Expected: the full test suite passes and `git diff --check` reports no errors.

- [ ] **Step 5: Commit documentation and remaining contract updates**

```powershell
git add README.md docs/camera-ai-pipeline.md tests
git commit -m "docs: describe neutral event routing policy"
```

- [ ] **Step 6: Review final diff and benchmark-cost invariants**

Run:

```powershell
git diff HEAD~4 --stat
git status --short
rg -n "analyze_with_trace|analyze_sequence" src/camera_ai/video_windows.py
```

Confirm there is still only one selected primary candidate passed to one VLM call per window, no new model dependency, and no unrelated or untracked file was staged.
