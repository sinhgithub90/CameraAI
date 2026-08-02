# Video Red Alert Cooldown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Skip Motion, Detection, routing, keyframe selection, and Qwen for 60 seconds of video time after a camera receives a verified red result, preserve one alert episode, and reverify the first eligible later window.

**Architecture:** Keep the existing global `VLMQueue` and single `VLMWorker`. Add a transport-independent, in-memory camera alert state store that is consulted when `VideoWindowProcessor` receives a raw window, before Motion; suppressed windows immediately produce an inherited result, while normal/recheck windows continue through the existing pipeline. Persist additive window context through `AnalysisStore`, `AlertStore`, and benchmark reports, keyed by `(analysis_id, camera_id)` so simultaneous demo videos remain independent.

**Tech Stack:** Python 3.11+, Pydantic, asyncio, FastAPI, OpenCV, pytest.

## Global Constraints

- Red cooldown is exactly `60.0` seconds of video event time.
- Orange watch recheck is exactly `15.0` seconds of video event time.
- Retry delays are `15`, `15`, `30`, then at most `60` seconds of processing time.
- A cooldown window skips Motion, Detection, routing, keyframe selection, and Qwen.
- A cooldown window records `motion_ms = detector_ms = keyframe_ms = qwen_ms = 0`.
- Effective inherited red must be distinct from a VLM-verified window result.
- A camera/analysis pair has at most one Qwen verification in flight.
- No signal overrides an active red cooldown in this MVP.
- Keep the existing queue, worker count, image endpoints, sync video endpoint, and existing API fields.
- New API and JSON fields are additive.
- Runtime cooldown state is in memory and may be lost on process restart.
- Do not claim latency improvement without a real API + Ollama benchmark.

---

## File Map

- Create `src/camera_ai/alert_cooldown.py`: state machine, atomic in-memory state store, gate decisions, and per-window alert context.
- Create `tests/test_alert_cooldown.py`: deterministic state transition tests without video or HTTP.
- Modify `src/camera_ai/vlm_policy.py`: add serialized cooldown/recheck reasons.
- Modify `src/camera_ai/video_windows.py`: apply cooldown before Motion and resume the full pipeline for rechecks.
- Modify `src/camera_ai/pipeline.py`: inject the state store and pass `analysis_id` as stream identity.
- Modify `src/camera_ai/schemas.py`: document the additive `suppressed` VLM lifecycle value.
- Modify `src/camera_ai/analysis_store.py`: persist alert context and expose cooldown aggregates.
- Modify `src/camera_ai/alert_store.py`: store one business alert episode separately from per-window compatibility alerts.
- Modify `src/camera_ai/queue.py`: pass stream identity and synchronize episode persistence after a processed window.
- Modify `apps/api/main.py`: construct one state store and reject overlapping analyses for one camera.
- Modify `apps/api/static/index.html`: label inherited alerts as unverified rather than newly confirmed.
- Modify `scripts/benchmark_pipeline.py`: emit cooldown counters and compact verification metadata.
- Modify focused tests under `tests/`: cover processor, worker/API, persistence, UI, and benchmark behavior.
- Modify `README.md` and `docs/benchmarks/README.md`: document the 60-second semantics and metrics.

---

### Task 1: Add the deterministic camera alert state machine

**Files:**
- Create: `src/camera_ai/alert_cooldown.py`
- Create: `tests/test_alert_cooldown.py`
- Modify: `src/camera_ai/vlm_policy.py`

**Interfaces:**
- Consumes: `AlertLevel`, `SceneAnalysis`, `AlertEvent`, and `VLMCallDecision`.
- Produces: `inspect_window(stream_id: str, camera_id: str, start_seconds: float, end_seconds: float, processing_now: float) -> WindowAdmission`, `claim_vlm(admission: WindowAdmission, base_decision: VLMCallDecision) -> CooldownGateDecision`, `record_result(gate: CooldownGateDecision, end_seconds: float, scene: SceneAnalysis, alert_event: AlertEvent | None, trace_valid: bool, processing_now: float) -> WindowAlertContext`, `record_failure(gate: CooldownGateDecision, processing_now: float) -> WindowAlertContext`, and `get(stream_id: str, camera_id: str) -> CameraAlertRuntime`.

- [ ] **Step 1: Write failing tests for initial red, suppression, camera isolation, and recheck transitions**

```python
# tests/test_alert_cooldown.py
from camera_ai.alert_cooldown import (
    AlertRuntimePhase,
    CooldownDisposition,
    InMemoryCameraAlertStateStore,
    WindowDisposition,
)
from camera_ai.event_models import AlertEvent, AlertStatus, Severity
from camera_ai.schemas import AlertLevel, SceneAnalysis
from camera_ai.vlm_policy import VLMCallDecision, VLMCallReason


def call_decision():
    return VLMCallDecision(
        call_vlm=True,
        reason=VLMCallReason.CANDIDATE_REQUIRES_VERIFICATION,
        candidate_id="candidate-1",
    )


def red_alert():
    return AlertEvent(
        alert_id="episode-1",
        camera_id="cam-a",
        event_type="traffic_accident",
        severity=Severity.HIGH,
        status=AlertStatus.PENDING_REVIEW,
        source_candidate_id="candidate-1",
    )


def test_red_result_suppresses_only_same_stream_camera_for_sixty_seconds():
    store = InMemoryCameraAlertStateStore()
    first_admission = store.inspect_window(
        stream_id="analysis-a", camera_id="cam-a",
        start_seconds=0.0, end_seconds=5.0,
        processing_now=100.0,
    )
    first = store.claim_vlm(first_admission, call_decision())
    store.record_result(
        gate=first, end_seconds=5.0,
        scene=SceneAnalysis(alert_level=AlertLevel.LOW),
        alert_event=red_alert(), trace_valid=True, processing_now=105.0,
    )

    blocked = store.inspect_window(
        stream_id="analysis-a", camera_id="cam-a",
        start_seconds=5.0, end_seconds=10.0,
        processing_now=106.0,
    )
    other = store.inspect_window(
        stream_id="analysis-b", camera_id="cam-b",
        start_seconds=5.0, end_seconds=10.0,
        processing_now=106.0,
    )

    assert blocked.disposition is WindowDisposition.SUPPRESS
    assert blocked.process_window is False
    assert blocked.active_alert_id == "episode-1"
    assert blocked.effective_level is AlertLevel.HIGH
    assert other.disposition is WindowDisposition.PROCESS_NORMAL


def test_first_window_at_recheck_is_forced_and_red_extends_deadline():
    store = InMemoryCameraAlertStateStore()
    first_admission = store.inspect_window(
        stream_id="analysis-a", camera_id="cam-a",
        start_seconds=0.0, end_seconds=5.0,
        processing_now=100.0,
    )
    first = store.claim_vlm(first_admission, call_decision())
    store.record_result(
        gate=first, end_seconds=5.0,
        scene=SceneAnalysis(alert_level=AlertLevel.HIGH),
        alert_event=red_alert(), trace_valid=True, processing_now=105.0,
    )
    recheck_admission = store.inspect_window(
        stream_id="analysis-a", camera_id="cam-a",
        start_seconds=65.0, end_seconds=70.0,
        processing_now=170.0,
    )
    recheck = store.claim_vlm(
        recheck_admission,
        VLMCallDecision(
            call_vlm=False, reason=VLMCallReason.STATIC_WINDOW
        ),
    )
    context = store.record_result(
        gate=recheck, end_seconds=70.0,
        scene=SceneAnalysis(alert_level=AlertLevel.HIGH),
        alert_event=None, trace_valid=True, processing_now=175.0,
    )

    assert recheck.disposition is CooldownDisposition.FORCE_RECHECK
    assert context.recheck is True
    assert context.episode_extended is True
    assert store.get("analysis-a", "cam-a").next_recheck_event_seconds == 130.0


def test_recheck_medium_enters_orange_watch_and_low_resolves():
    store = InMemoryCameraAlertStateStore.seeded_red(
        stream_id="analysis-a", camera_id="cam-a",
        active_alert_id="episode-1", next_recheck_event_seconds=65.0,
    )
    medium_admission = store.inspect_window(
        stream_id="analysis-a", camera_id="cam-a",
        start_seconds=65.0, end_seconds=70.0,
        processing_now=170.0,
    )
    medium_gate = store.claim_vlm(medium_admission, call_decision())
    store.record_result(
        gate=medium_gate, end_seconds=70.0,
        scene=SceneAnalysis(alert_level=AlertLevel.MEDIUM),
        alert_event=None, trace_valid=True, processing_now=175.0,
    )
    assert store.get("analysis-a", "cam-a").phase is AlertRuntimePhase.ORANGE_WATCH
    assert store.get("analysis-a", "cam-a").next_recheck_event_seconds == 85.0

    low_admission = store.inspect_window(
        stream_id="analysis-a", camera_id="cam-a",
        start_seconds=85.0, end_seconds=90.0,
        processing_now=190.0,
    )
    low_gate = store.claim_vlm(low_admission, call_decision())
    resolved = store.record_result(
        gate=low_gate, end_seconds=90.0,
        scene=SceneAnalysis(alert_level=AlertLevel.LOW),
        alert_event=None, trace_valid=True, processing_now=195.0,
    )
    assert resolved.episode_resolved is True
    assert store.get("analysis-a", "cam-a").phase is AlertRuntimePhase.NORMAL
```

- [ ] **Step 2: Run the state tests and verify the new module is missing**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_alert_cooldown.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: camera_ai.alert_cooldown`.

- [ ] **Step 3: Add cooldown/recheck reasons to the existing policy contract**

```python
# src/camera_ai/vlm_policy.py
class VLMCallReason(str, Enum):
    STATIC_WINDOW = "static_window"
    NO_USABLE_FRAMES = "no_usable_frames"
    CANDIDATE_REQUIRES_VERIFICATION = "candidate_requires_verification"
    ACTIVE_ALERT_COOLDOWN = "active_alert_cooldown"
    ACTIVE_ALERT_RECHECK = "active_alert_recheck"
    CAMERA_VLM_INFLIGHT = "camera_vlm_inflight"
```

- [ ] **Step 4: Implement the state models and locked in-memory transitions**

Create `alert_cooldown.py` with these exact constants and public types:

```python
RED_COOLDOWN_SECONDS = 60.0
ORANGE_RECHECK_SECONDS = 15.0
RETRY_DELAYS_SECONDS = (15.0, 15.0, 30.0, 60.0)

class AlertRuntimePhase(str, Enum):
    NORMAL = "normal"
    ALERT_ACTIVE = "alert_active"
    ORANGE_WATCH = "orange_watch"
    ALERT_ACTIVE_UNVERIFIED = "alert_active_unverified"

class VerificationStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    VERIFIED = "verified"
    SUPPRESSED = "suppressed"
    FAILED = "failed"

class CooldownDisposition(str, Enum):
    USE_BASE_POLICY = "use_base_policy"
    SUPPRESS = "suppress"
    FORCE_RECHECK = "force_recheck"

class WindowDisposition(str, Enum):
    PROCESS_NORMAL = "process_normal"
    SUPPRESS = "suppress"
    PROCESS_RECHECK = "process_recheck"

class CameraAlertRuntime(BaseModel):
    stream_id: str
    camera_id: str
    phase: AlertRuntimePhase = AlertRuntimePhase.NORMAL
    current_level: AlertLevel = AlertLevel.LOW
    active_alert_id: str | None = None
    event_type: str | None = None
    next_recheck_event_seconds: float | None = None
    retry_due_processing_at: float | None = None
    verification_status: VerificationStatus = VerificationStatus.NOT_REQUIRED
    vlm_inflight: bool = False
    retry_count: int = 0
    state_version: int = 0

class WindowAdmission(BaseModel):
    stream_id: str
    camera_id: str
    start_seconds: float
    end_seconds: float
    disposition: WindowDisposition
    process_window: bool
    state: AlertRuntimePhase = AlertRuntimePhase.NORMAL
    effective_level: AlertLevel = AlertLevel.LOW
    active_alert_id: str | None = None
    event_type: str | None = None
    reason: VLMCallReason | None = None
    recheck: bool = False
    next_recheck_event_seconds: float | None = None

class CooldownGateDecision(BaseModel):
    stream_id: str
    camera_id: str
    start_seconds: float
    end_seconds: float
    disposition: CooldownDisposition
    call_vlm: bool
    reason: VLMCallReason
    priority: Priority = Priority.LOW
    candidate_id: str | None = None
    effective_level: AlertLevel
    active_alert_id: str | None = None
    recheck: bool = False

    def as_vlm_call_decision(self) -> VLMCallDecision:
        return VLMCallDecision(
            call_vlm=self.call_vlm,
            reason=self.reason,
            priority=self.priority,
            candidate_id=self.candidate_id,
        )

class WindowAlertContext(BaseModel):
    stream_id: str = "default"
    camera_id: str = "unknown"
    state: AlertRuntimePhase = AlertRuntimePhase.NORMAL
    detected_level: AlertLevel | None = None
    effective_level: AlertLevel = AlertLevel.LOW
    verification_status: VerificationStatus = VerificationStatus.NOT_REQUIRED
    source: str = "none"
    window_start_seconds: float = 0.0
    window_end_seconds: float = 0.0
    active_alert_id: str | None = None
    event_type: str | None = None
    recheck: bool = False
    episode_created: bool = False
    episode_extended: bool = False
    episode_resolved: bool = False
    next_recheck_event_seconds: float | None = None
```

Define a `CameraAlertStateStore(Protocol)` with the five exact method
signatures in this task's Interfaces block, and make
`InMemoryCameraAlertStateStore` implement that protocol.

Use `threading.RLock` around every read/transition. `inspect_window` returns
`SUPPRESS` before any inference when an alert is active and not due, and
`PROCESS_RECHECK` for the first eligible window. `claim_vlm` atomically sets
`vlm_inflight=True` whenever its returned decision calls Qwen; it must not force
a recheck for `NO_USABLE_FRAMES`. `record_result` must clear the inflight flag
in every branch and emit `WindowAlertContext` fields
`detected_level`, `effective_level`, `verification_status`, `active_alert_id`,
`source`, `recheck`, `episode_created`, `episode_extended`, and
`episode_resolved`.

Resolve a verified level from `AlertEvent.severity` first: `HIGH` and
`CRITICAL` map to `AlertLevel.HIGH`, `MEDIUM` maps to `AlertLevel.MEDIUM`, and
only a missing alert event falls back to `SceneAnalysis.alert_level`. This
matches the existing benchmark rule where confirmed event severity overrides
the raw scene level.

Implement `WindowAdmission.normal(...)` and `CooldownGateDecision.from_base(...)`
for the no-store compatibility path and
`InMemoryCameraAlertStateStore.seeded_red(...)` as a deterministic
test factory that inserts one `ALERT_ACTIVE` runtime under the same lock. These
helpers must use the exact models above rather than mutable dictionaries.

- [ ] **Step 5: Add failure/backoff and in-flight tests**

```python
def test_failed_recheck_keeps_red_and_uses_bounded_retry_backoff():
    store = InMemoryCameraAlertStateStore.seeded_red(
        stream_id="analysis-a", camera_id="cam-a",
        active_alert_id="episode-1", next_recheck_event_seconds=65.0,
    )
    retry_times = []
    now = 200.0
    for expected_delay in (15.0, 15.0, 30.0, 60.0, 60.0):
        admission = store.inspect_window(
            stream_id="analysis-a", camera_id="cam-a",
            start_seconds=65.0, end_seconds=70.0,
            processing_now=now,
        )
        gate = store.claim_vlm(admission, call_decision())
        context = store.record_failure(gate=gate, processing_now=now)
        retry_times.append(store.get("analysis-a", "cam-a").retry_due_processing_at)
        assert context.effective_level is AlertLevel.HIGH
        now += expected_delay
    assert retry_times == [215.0, 230.0, 260.0, 320.0, 380.0]


def test_second_window_is_suppressed_while_same_camera_vlm_is_inflight():
    store = InMemoryCameraAlertStateStore()
    first_admission = store.inspect_window(
        stream_id="analysis-a", camera_id="cam-a",
        start_seconds=0.0, end_seconds=5.0,
        processing_now=100.0,
    )
    first = store.claim_vlm(first_admission, call_decision())
    second = store.inspect_window(
        stream_id="analysis-a", camera_id="cam-a",
        start_seconds=5.0, end_seconds=10.0,
        processing_now=101.0,
    )
    assert first.call_vlm is True
    assert second.process_window is False
    assert second.disposition is WindowDisposition.SUPPRESS
```

- [ ] **Step 6: Run tests and commit the state machine**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_alert_cooldown.py tests/test_vlm_policy.py -q`

Expected: all tests pass.

```powershell
git add src/camera_ai/alert_cooldown.py src/camera_ai/vlm_policy.py tests/test_alert_cooldown.py
git commit -m "feat: add camera red cooldown state"
```

---

### Task 2: Apply cooldown before Motion and resume the full pipeline for recheck

**Files:**
- Modify: `src/camera_ai/video_windows.py:63-300`
- Modify: `src/camera_ai/pipeline.py:55-100,640-648`
- Modify: `src/camera_ai/schemas.py:158-171`
- Modify: `tests/test_pipeline_events.py`
- Modify: `tests/test_video_pipeline.py`

**Interfaces:**
- Consumes: `CameraAlertStateStore` and `CooldownGateDecision` from Task 1.
- Produces: `ProcessedVideoWindow.alert_context: WindowAlertContext` and `SecurityAIPipeline.process_video_window(window: RawVideoWindow, camera_id: str = "unknown", stream_id: str = "default") -> ProcessedVideoWindow`.

- [ ] **Step 1: Write a processor test proving every inference stage is skipped during cooldown**

```python
class CountingDetector(Detector):
    def __init__(self):
        self.calls = 0

    def detect(self, frame):
        self.calls += 1
        return super().detect(frame)


def test_red_cooldown_skips_motion_detector_and_qwen():
    state = InMemoryCameraAlertStateStore.seeded_red(
        stream_id="analysis-a", camera_id="cam-a",
        active_alert_id="episode-1", next_recheck_event_seconds=65.0,
    )
    detector = CountingDetector()
    vlm = VLM()
    processor = VideoWindowProcessor(
        detector=detector, vlm=vlm, yolo_fps=1.0,
        max_keyframes=2, alert_state_store=state,
    )

    result = processor.process(
        raw_window().model_copy(
            update={"window_index": 1, "start_seconds": 5.0}
        ),
        camera_id="cam-a", stream_id="analysis-a",
    )

    assert detector.calls == 0
    assert vlm.calls == 0
    assert result.vlm_call.reason is VLMCallReason.ACTIVE_ALERT_COOLDOWN
    assert result.timing.model_dump() == {
        "total_ms": 0.0, "motion_ms": 0.0, "detector_ms": 0.0,
        "keyframe_ms": 0.0, "qwen_ms": 0.0, "queue_wait_ms": 0.0,
        "wall_clock_ms": 0.0,
    }
    assert result.alert_context.verification_status is VerificationStatus.SUPPRESSED
    assert result.alert_context.effective_level is AlertLevel.HIGH
    assert result.scene.alert_level is AlertLevel.HIGH
```

- [ ] **Step 2: Run the focused test and verify constructor/signature failure**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_pipeline_events.py::test_red_cooldown_skips_motion_detector_and_qwen -q`

Expected: FAIL because `SecurityAIPipeline` does not accept `alert_state_store`.

- [ ] **Step 3: Inspect camera state before Motion and return immediately for suppressed windows**

Add optional `alert_state_store: CameraAlertStateStore | None = None` to
`SecurityAIPipeline.__init__` and `VideoWindowProcessor.__init__`. Pass
`stream_id: str = "default"` through `process_video_window`.

Add `alert_context: WindowAlertContext = Field(default_factory=WindowAlertContext)`
to `ProcessedVideoWindow`; the defaults defined in Task 1 keep existing direct
constructors compatible.

At the first line of `VideoWindowProcessor.process`, inspect the raw window:

```python
admission = (
    self.alert_state_store.inspect_window(
        stream_id=stream_id,
        camera_id=camera_id,
        start_seconds=window.start_seconds,
        end_seconds=window.end_seconds,
        processing_now=time.monotonic(),
    )
    if self.alert_state_store is not None
    else WindowAdmission.normal(
        stream_id=stream_id,
        camera_id=camera_id,
        start_seconds=window.start_seconds,
        end_seconds=window.end_seconds,
    )
)
if not admission.process_window:
    return self._build_suppressed_window(window, camera_id, admission)
```

`_build_suppressed_window` must not instantiate `MotionDetector`, call any
detector, route candidates, select keyframes, or call VLM. It returns empty
detections/candidates, an empty `QwenInputSummary`, all-zero `StageTiming`, a
minimal `VideoWindowObservation` using the raw window boundaries, and a
degraded-false inherited scene with summary
`"Cảnh báo đang hoạt động; cửa sổ này không được Qwen xác minh lại."`.

- [ ] **Step 4: Claim the VLM call after normal/recheck windows finish cheap stages**

Keep the existing Motion, Detection, keyframe, observation, candidate, and
base-policy code for admitted windows. Immediately after `decide_vlm_call`,
call:

```python
gate = (
    self.alert_state_store.claim_vlm(admission, base_vlm_call)
    if self.alert_state_store is not None
    else CooldownGateDecision.from_base(admission, base_vlm_call)
)
vlm_call = gate.as_vlm_call_decision()
```

For `PROCESS_RECHECK`, `claim_vlm` forces Qwen even when the base decision is
`STATIC_WINDOW`, but never when it is `NO_USABLE_FRAMES`. Call
`analyze_with_trace` with `candidate=None` when no candidate exists so the
existing generic scene schema is used.

- [ ] **Step 5: Record valid, degraded, and unexpected VLM outcomes**

After Qwen returns, call `record_result` with `trace.raw_output_valid`. If the
trace is degraded or invalid during a recheck, call `record_failure` instead.
Wrap unexpected analyzer exceptions into a degraded `VLMAnalysisTrace`, call
`record_failure`, and return a completed degraded window so it cannot remain
pending forever.

The final `ProcessedVideoWindow` must carry `alert_context`, and its `scene`
must use the effective inherited level only for suppressed/failed active-alert
windows. `alert_context.detected_level` remains `None` when Qwen was not called.

- [ ] **Step 6: Add recheck and error tests**

```python
class ResultVLM(VLM):
    def __init__(self, result):
        super().__init__()
        self.result = result

    def _scene(self):
        return self.result


class RaisingVLM(VLM):
    def analyze_with_trace(self, frames, detections, *, candidate=None):
        self.calls += 1
        raise RuntimeError("qwen failed")


def test_due_static_window_forces_one_qwen_recheck():
    state = InMemoryCameraAlertStateStore.seeded_red(
        stream_id="analysis-a", camera_id="cam-a",
        active_alert_id="episode-1", next_recheck_event_seconds=65.0,
    )
    vlm = ResultVLM(SceneAnalysis(alert_level=AlertLevel.LOW))
    processor = VideoWindowProcessor(
        detector=EmptyDetector(), vlm=vlm, yolo_fps=2,
        max_keyframes=2, alert_state_store=state,
    )
    result = processor.process(
        static_window().model_copy(
            update={"window_index": 13, "start_seconds": 65.0}
        ),
        camera_id="cam-a", stream_id="analysis-a",
    )
    assert vlm.calls == 1
    assert result.vlm_call.reason is VLMCallReason.ACTIVE_ALERT_RECHECK
    assert result.alert_context.episode_resolved is True


def test_failed_recheck_returns_completed_degraded_window_and_keeps_red():
    state = InMemoryCameraAlertStateStore.seeded_red(
        stream_id="analysis-a", camera_id="cam-a",
        active_alert_id="episode-1", next_recheck_event_seconds=65.0,
    )
    processor = VideoWindowProcessor(
        detector=EmptyDetector(), vlm=RaisingVLM(), yolo_fps=2,
        max_keyframes=2, alert_state_store=state,
    )
    result = processor.process(
        static_window().model_copy(
            update={"window_index": 13, "start_seconds": 65.0}
        ),
        camera_id="cam-a", stream_id="analysis-a",
    )
    assert result.scene.degraded is True
    assert result.scene.alert_level is AlertLevel.HIGH
    assert result.alert_context.verification_status is VerificationStatus.FAILED
    assert state.get("analysis-a", "cam-a").active_alert_id == "episode-1"
```

- [ ] **Step 7: Run processor regressions and commit**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_pipeline_events.py tests/test_video_pipeline.py tests/test_vlm_policy.py -q`

Expected: all tests pass.

```powershell
git add src/camera_ai/video_windows.py src/camera_ai/pipeline.py src/camera_ai/schemas.py tests/test_pipeline_events.py tests/test_video_pipeline.py
git commit -m "feat: suppress qwen during active red alerts"
```

---

### Task 3: Persist window verification context and one alert episode

**Files:**
- Modify: `src/camera_ai/analysis_store.py`
- Modify: `src/camera_ai/alert_store.py`
- Modify: `tests/test_analysis_store.py`
- Modify: `tests/test_alert_store.py`

**Interfaces:**
- Consumes: `ProcessedVideoWindow.alert_context` from Task 2.
- Produces: `AlertEpisode`, `AlertStore.apply_episode_context(context: WindowAlertContext) -> None`, `AlertStore.get_episode(alert_id: str) -> AlertEpisode | None`, and additive `event_metadata.alert_context`.

- [ ] **Step 1: Write failing persistence tests**

```python
def episode_context(*, created=False, extended=False):
    return WindowAlertContext(
        stream_id="analysis-a",
        camera_id="cam-a",
        state=AlertRuntimePhase.ALERT_ACTIVE,
        detected_level=AlertLevel.HIGH,
        effective_level=AlertLevel.HIGH,
        verification_status=VerificationStatus.VERIFIED,
        source="window_verification",
        active_alert_id="episode-1",
        event_type="traffic_accident",
        episode_created=created,
        episode_extended=extended,
        next_recheck_event_seconds=65.0,
    )


def suppressed_processed_window(active_alert_id):
    context = WindowAlertContext(
        stream_id="analysis-a",
        camera_id="cam-a",
        state=AlertRuntimePhase.ALERT_ACTIVE,
        effective_level=AlertLevel.HIGH,
        verification_status=VerificationStatus.SUPPRESSED,
        source="inherited_active_alert",
        active_alert_id=active_alert_id,
        event_type="traffic_accident",
        next_recheck_event_seconds=65.0,
    )
    return ProcessedVideoWindow(
        scene=SceneAnalysis(summary="inherited", alert_level=AlertLevel.HIGH),
        qwen_input=QwenInputSummary(frame_count=2),
        timing=StageTiming(),
        observation=VideoWindowObservation(
            camera_id="cam-a", window_id="cam-a_000001",
            start_ms=5000, end_ms=10000,
        ),
        vlm_call=VLMCallDecision(
            call_vlm=False, reason=VLMCallReason.ACTIVE_ALERT_COOLDOWN,
        ),
        alert_context=context,
    )


@pytest.mark.asyncio
async def test_suppressed_window_persists_inherited_alert_context():
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-a", camera_id="cam-a"))
    await store.append_window("analysis-a", pending_window("window-2"))
    processed = suppressed_processed_window(active_alert_id="episode-1")
    await store.complete_processed_window(
        "analysis-a", "window-2", processed, qwen_ms=0.0
    )
    saved = (await store.get("analysis-a")).windows[0]
    assert saved.vlm.status == "suppressed"
    assert saved.vlm.skipped is True
    assert saved.security.alert_level is AlertLevel.HIGH
    assert saved.timing.total_ms == 0
    assert saved.timing.motion_ms == 0
    assert saved.timing.detector_ms == 0
    assert saved.timing.keyframe_ms == 0
    assert saved.timing.qwen_ms == 0
    assert saved.event_metadata["alert_context"]["verification_status"] == "suppressed"
    assert saved.event_metadata["alert_context"]["active_alert_id"] == "episode-1"


@pytest.mark.asyncio
async def test_repeated_red_updates_one_episode_instead_of_creating_another():
    store = InMemoryAlertStore(event_bus=InProcessEventBus())
    await store.apply_episode_context(episode_context(created=True))
    await store.apply_episode_context(episode_context(extended=True))
    assert len(store._episodes) == 1
    assert store._episodes["episode-1"].extension_count == 1
    assert store._episodes["episode-1"].status == "active"
```

- [ ] **Step 2: Run tests and verify missing fields/methods**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_analysis_store.py tests/test_alert_store.py -q`

Expected: FAIL because `suppressed` persistence and episode methods do not exist.

- [ ] **Step 3: Persist additive alert context in analysis windows**

Update `complete_processed_window` so `vlm_status` is:

```python
vlm_status = (
    "suppressed"
    if processed.alert_context.verification_status is VerificationStatus.SUPPRESSED
    else "skipped"
    if not processed.vlm_call.call_vlm
    else "completed"
)
```

Set `VLMResult.skipped=True` for both `suppressed` and `skipped`, copy the
effective scene into `security`, and add:

```python
"alert_context": processed.alert_context.model_dump(mode="json")
```

to `event_metadata`. Update the `VLMResult.status` description in `schemas.py`
to list `pending | completed | skipped | suppressed`.

- [ ] **Step 4: Add the business episode model and idempotent store update**

Add `AlertEpisode` with fields `id`, `camera_id`, `event_type`, `status`,
`current_level`, `started_event_seconds`, `last_verified_event_seconds`,
`next_recheck_event_seconds`, `extension_count`, and `resolved_event_seconds`.
Extend `AlertStore` and `InMemoryAlertStore` with:

```python
async def apply_episode_context(self, context: WindowAlertContext) -> None
async def get_episode(self, alert_id: str) -> AlertEpisode | None
```

`apply_episode_context` creates only when `episode_created`, increments once
when `episode_extended`, updates medium watch state, and marks the same record
resolved when `episode_resolved`. Publish `alert.episode_created`,
`alert.episode_extended`, and `alert.episode_resolved` events with `alert_id` and
`camera_id`.

- [ ] **Step 5: Run persistence tests and commit**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_analysis_store.py tests/test_alert_store.py tests/test_event_contracts.py -q`

Expected: all tests pass.

```powershell
git add src/camera_ai/analysis_store.py src/camera_ai/alert_store.py src/camera_ai/schemas.py tests/test_analysis_store.py tests/test_alert_store.py
git commit -m "feat: persist inherited alerts and episodes"
```

---

### Task 4: Wire stream identity, worker updates, and camera admission

**Files:**
- Modify: `src/camera_ai/queue.py`
- Modify: `src/camera_ai/analysis_store.py`
- Modify: `apps/api/main.py`
- Modify: `tests/test_queue.py`
- Modify: `tests/test_async_video_response.py`
- Modify: `tests/test_analysis_endpoint.py`

**Interfaces:**
- Consumes: `SecurityAIPipeline.process_video_window(window: RawVideoWindow, camera_id: str, stream_id: str)`, and `AlertStore.apply_episode_context(context: WindowAlertContext) -> None`.
- Produces: `AnalysisStore.has_active_camera(camera_id) -> bool` and HTTP `409` for overlapping video analysis of one camera.

- [ ] **Step 1: Write worker and API admission tests**

```python
def raw_video_task(*, analysis_id, camera_id):
    return VLMTask(
        priority=3,
        enqueued_at=100.0,
        task_id="window-0",
        alert_id="window-0",
        analysis_id=analysis_id,
        camera_id=camera_id,
        window_index=0,
        raw_window=RawVideoWindow(
            window_index=0, start_seconds=0.0, observations=[]
        ),
    )


def completed_processed_window_with_created_episode(alert_id):
    return ProcessedVideoWindow(
        scene=SceneAnalysis(alert_level=AlertLevel.HIGH),
        qwen_input=QwenInputSummary(frame_count=2),
        timing=StageTiming(qwen_ms=50.0, total_ms=50.0),
        observation=VideoWindowObservation(
            camera_id="cam-a", window_id="cam-a_000000",
            start_ms=0, end_ms=5000,
        ),
        vlm_call=VLMCallDecision(
            call_vlm=True,
            reason=VLMCallReason.CANDIDATE_REQUIRES_VERIFICATION,
        ),
        alert_context=WindowAlertContext(
            stream_id="analysis-a",
            camera_id="cam-a",
            state=AlertRuntimePhase.ALERT_ACTIVE,
            detected_level=AlertLevel.HIGH,
            effective_level=AlertLevel.HIGH,
            verification_status=VerificationStatus.VERIFIED,
            source="window_verification",
            active_alert_id=alert_id,
            event_type="traffic_accident",
            episode_created=True,
            next_recheck_event_seconds=65.0,
        ),
    )


@pytest.mark.asyncio
async def test_video_worker_passes_analysis_id_and_applies_episode_context():
    pipeline = MagicMock()
    processed = completed_processed_window_with_created_episode("episode-1")
    pipeline.process_video_window.return_value = processed
    alert_store = AsyncMock()
    analysis_store = AsyncMock()
    worker = VLMWorker(
        queue=VLMQueue(), pipeline=pipeline, alert_store=alert_store,
        analysis_store=analysis_store, event_bus=AsyncMock(),
    )
    task = raw_video_task(analysis_id="analysis-a", camera_id="cam-a")
    await worker._process_task(task)
    pipeline.process_video_window.assert_called_once_with(
        task.raw_window, "cam-a", stream_id="analysis-a"
    )
    alert_store.apply_episode_context.assert_awaited_once_with(
        processed.alert_context
    )


@pytest.mark.asyncio
async def test_second_active_upload_for_same_camera_returns_409(monkeypatch):
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-a", camera_id="cam-a"))
    monkeypatch.setattr(main, "analysis_store", store)
    upload = UploadFile(filename="second.mp4", file=BytesIO(b"video"))
    with pytest.raises(HTTPException) as error:
        await main.analyze_video_async(upload, camera_id="cam-a")
    assert error.value.status_code == 409
```

- [ ] **Step 2: Run tests and verify missing worker/admission behavior**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_queue.py tests/test_async_video_response.py -q`

Expected: FAIL because the worker omits `stream_id`, does not apply episodes,
and the API accepts a second active analysis.

- [ ] **Step 3: Extract one worker iteration and wire processed video context**

Extract `VLMWorker._process_task(task)` from the run loop so it is directly
testable. For a raw video task:

```python
processed = await asyncio.to_thread(
    self._pipeline.process_video_window,
    task.raw_window,
    task.camera_id,
    stream_id=task.analysis_id,
)
await self._analysis_store.complete_processed_window(
    task.analysis_id, task.alert_id, processed, processed.timing.qwen_ms
)
await self._alert_store.update_vlm(
    task.alert_id, processed.scene, qwen_ms=processed.timing.qwen_ms
)
await self._alert_store.apply_episode_context(processed.alert_context)
```

Keep the existing per-window compatibility alert update and image-task branch.
The new `_episodes` collection is the business-level deduplicated view; legacy
window alerts remain available to existing polling clients. Preserve queue and
wall-clock timing assignment before persistence.

- [ ] **Step 4: Add active-camera lookup and API guard**

Add to `AnalysisStore`:

```python
async def has_active_camera(self, camera_id: str) -> bool
```

The in-memory implementation returns true only for `reading` or `queued`
analyses. In `analyze_video_async`, perform this check before reading the full
upload and raise:

```python
raise HTTPException(
    status_code=409,
    detail="camera already has an active video analysis",
)
```

Create one shared `InMemoryCameraAlertStateStore` at API startup and inject it
through `_build_pipeline(alert_state_store)`.

- [ ] **Step 5: Run async integration tests and commit**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_queue.py tests/test_async_video_response.py tests/test_analysis_endpoint.py tests/test_streaming_video_producer.py -q`

Expected: all tests pass.

```powershell
git add src/camera_ai/queue.py src/camera_ai/analysis_store.py apps/api/main.py tests/test_queue.py tests/test_async_video_response.py tests/test_analysis_endpoint.py
git commit -m "feat: wire per-camera cooldown into video worker"
```

---

### Task 5: Expose inherited verification clearly in the demo UI

**Files:**
- Modify: `apps/api/static/index.html:389-435`
- Modify: `tests/test_async_video_ui.py`

**Interfaces:**
- Consumes: `window.event_metadata.alert_context` and `window.vlm.status`.
- Produces: visible inherited/unverified labels without changing polling.

- [ ] **Step 1: Add a failing UI source contract test**

```python
def test_video_ui_labels_inherited_red_as_not_reverified():
    source = UI_FILE.read_text(encoding="utf-8")
    assert "inherited_active_alert" in source
    assert "Chưa được Qwen xác minh lại" in source
    assert "active_alert_cooldown" in source
```

- [ ] **Step 2: Run the UI test and verify the labels are absent**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_async_video_ui.py -q`

Expected: FAIL on the first missing source string.

- [ ] **Step 3: Render effective and detected state separately**

In `renderVideoWindows`, read:

```javascript
const alertContext = window.event_metadata?.alert_context || {};
const inherited = alertContext.source === 'inherited_active_alert';
const verificationText = inherited
  ? 'Chưa được Qwen xác minh lại — kế thừa cảnh báo đang hoạt động'
  : status === 'completed'
    ? 'Đã được Qwen xác minh'
    : 'Không gọi Qwen';
```

Add `Verification` and `Active alert` rows. Keep the red card level from
`window.security.alert_level`, but never show `suppressed` as completed.

- [ ] **Step 4: Run UI tests and commit**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_async_video_ui.py tests/test_analysis_endpoint.py -q`

Expected: all tests pass.

```powershell
git add apps/api/static/index.html tests/test_async_video_ui.py
git commit -m "feat: label inherited video alerts in demo"
```

---

### Task 6: Add cooldown metrics to compact benchmark reports

**Files:**
- Modify: `scripts/benchmark_pipeline.py:90-190`
- Modify: `tests/test_benchmark_cli.py`
- Modify: `docs/benchmarks/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `event_metadata.alert_context`, `event_metadata.vlm_call`, and compact timing.
- Produces: cooldown counters in `performance_summary` and verification fields per window.

- [ ] **Step 1: Write a failing compact-report test**

```python
def test_video_report_counts_cooldown_suppression_and_rechecks(tmp_path):
    payload = analysis_payload("high", "high", "high")
    payload["windows"][0]["event_metadata"] = {
        "vlm_call": {"call_vlm": True, "reason": "candidate_requires_verification"},
        "alert_context": {
            "verification_status": "verified", "episode_created": True,
            "episode_extended": False, "recheck": False,
            "source": "window_verification", "active_alert_id": "episode-1",
        },
    }
    payload["windows"][1]["event_metadata"] = {
        "vlm_call": {"call_vlm": False, "reason": "active_alert_cooldown"},
        "alert_context": {
            "verification_status": "suppressed", "episode_created": False,
            "episode_extended": False, "recheck": False,
            "source": "inherited_active_alert", "active_alert_id": "episode-1",
        },
    }
    payload["windows"][2]["event_metadata"] = {
        "vlm_call": {"call_vlm": True, "reason": "active_alert_recheck"},
        "alert_context": {
            "verification_status": "verified", "episode_created": False,
            "episode_extended": True, "recheck": True,
            "source": "window_verification", "active_alert_id": "episode-1",
        },
    }
    report = build_video_report(tmp_path / "event.mp4", "analysis-a", payload)
    assert report["performance_summary"]["vlm_suppressed_by_cooldown"] == 1
    assert report["performance_summary"]["cooldown_suppression_rate"] == 1 / 3
    assert report["performance_summary"]["red_episodes_created"] == 1
    assert report["performance_summary"]["red_rechecks"] == 1
    assert report["performance_summary"]["red_cooldown_extensions"] == 1
    assert report["windows"][1]["qwen"]["verified"] is False
    assert report["windows"][1]["qwen"]["reason"] == "active_alert_cooldown"
```

- [ ] **Step 2: Run the benchmark test and verify metrics are missing**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_benchmark_cli.py::test_video_report_counts_cooldown_suppression_and_rechecks -q`

Expected: FAIL with missing `vlm_suppressed_by_cooldown`.

- [ ] **Step 3: Add compact counters and per-window verification fields**

For every window, copy `vlm_call.reason`, `alert_context.verification_status`,
`alert_context.source`, and `alert_context.active_alert_id` into the compact
`qwen` object. Aggregate exact counts for cooldown suppression, created
episodes, rechecks, extensions, and failed rechecks. Define
`cooldown_suppression_rate` as suppressed cooldown windows divided by all
windows, returning `0.0` for an empty report.

- [ ] **Step 4: Document the report contract and demo command**

Document that inherited red windows are not new Qwen confirmations, cooldown
uses video event time, and suppressed windows skip Motion, Detection, routing,
keyframe selection, and Qwen. Document that the existing `--input-dir` command
assigns each video stem as `camera_id`, and that a source must extend at least
60 seconds beyond its first confirmed red window to observe a recheck.

- [ ] **Step 5: Run benchmark tests and commit**

Run: `$env:PYTHONPATH='src'; python -m pytest tests/test_benchmark_cli.py tests/test_benchmark_metrics.py -q`

Expected: all tests pass.

```powershell
git add scripts/benchmark_pipeline.py tests/test_benchmark_cli.py README.md docs/benchmarks/README.md
git commit -m "feat: report red cooldown benchmark metrics"
```

---

### Task 7: Verify the complete MVP and run the real demo benchmark when available

**Files:**
- Test: all repository tests
- Output: `runs/alerts/*.json` (ignored by git)

**Interfaces:**
- Consumes: completed implementation from Tasks 1–6.
- Produces: regression evidence and, when API/Ollama are running, a measured cooldown report.

- [ ] **Step 1: Run formatting/static repository checks**

Run: `git diff --check`

Expected: no output and exit code 0.

- [ ] **Step 2: Run the focused cooldown suite**

Run:

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/test_alert_cooldown.py tests/test_pipeline_events.py tests/test_analysis_store.py tests/test_alert_store.py tests/test_queue.py tests/test_async_video_response.py tests/test_analysis_endpoint.py tests/test_async_video_ui.py tests/test_benchmark_cli.py tests/test_benchmark_metrics.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run the complete regression suite**

Run: `$env:PYTHONPATH='src'; python -m pytest -q`

Expected: all tests pass with no unexpected skip or collection error.

- [ ] **Step 4: Start the existing API only for an authorized local benchmark**

Confirm Ollama is listening on `127.0.0.1:11434`, then start the API using the
existing README command and production-default model
`qwen3-vl:4b-instruct-q4_K_M`. Do not change model, keyframe count, Motion FPS,
or YOLO FPS during this comparison.

- [ ] **Step 5: Run the existing one-minute demo video to verify suppression**

Run:

```powershell
python -m scripts.benchmark_pipeline `
  --input-file "D:\CongViec\CameraAI\CameraAI\videos\RoadAccidents006_x264.mp4" `
  --output-dir "D:\CongViec\CameraAI\CameraAI\runs\alerts" `
  --base-url "http://127.0.0.1:8000"
```

Expected report properties:

- one created red episode;
- subsequent 5-second windows use `active_alert_cooldown`;
- suppressed windows have zero Motion, Detection, keyframe, and Qwen timings;
- inherited windows retain red effective state but have `verified=false`;
- no duplicate episode is created by an extension.

`RoadAccidents006_x264.mp4` ends at about one minute, so an alert confirmed in
its first window normally places the recheck beyond end-of-file. Treat absence
of a real recheck in this report as expected; Task 2's deterministic 65-second
window test proves recheck behavior. Run the same command on a controlled video
longer than 70 seconds when one is available, without fabricating a passing
claim from the one-minute fixture.

- [ ] **Step 6: Compare measured Qwen calls and latency without overclaiming**

Record the new `vlm_call_rate`, `vlm_suppressed_by_cooldown`,
`cooldown_suppression_rate`, `processing_p95_ms`, and `windows_over_budget`.
State explicitly when the available video ends before the first recheck or
does not produce a verified early red.

- [ ] **Step 7: Commit any verification-only documentation corrections**

If the measured command or documented field names required correction, commit
only those corrections:

```powershell
git add README.md docs/benchmarks/README.md
git commit -m "docs: clarify red cooldown benchmark"
```

If no correction was needed, do not create an empty commit.
