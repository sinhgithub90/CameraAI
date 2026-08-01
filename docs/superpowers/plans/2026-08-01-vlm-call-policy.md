# Conservative VLM Call Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Skip Qwen for static async-video windows, preserve one Qwen verification for every meaningful candidate, and report whether each five-second window meets the 5,000 ms processing budget.

**Architecture:** Add a transport-independent policy module between Event Router and VLM invocation. Persist its typed decision on `ProcessedVideoWindow`, project it into the existing async result additively, and derive call-rate/SLO metrics in benchmark output without changing the HTTP contract's existing fields.

**Tech Stack:** Python 3.11+, Pydantic v2, OpenCV, pytest, FastAPI async analysis store, existing Qwen/Ollama adapter.

## Global Constraints

- Apply only to async video processing; do not change the sync image/video gate.
- Static means no routed candidate after Motion, YOLO, and any enabled specialized detector.
- Call Qwen once for `unknown_motion`, person, vehicle, person-vehicle, multi-person, and temporally confirmed fire candidates.
- Keep bbox internally and do not add raw bbox coordinates to the Qwen prompt.
- A skipped static window remains a green JSON window with `qwen_ms = 0`.
- Processing SLO is p95 at or below 5,000 ms, excluding queue wait; report violations but do not claim compliance without a real Ollama benchmark.
- Do not introduce tracking, a 2B/4B cascade, additional queues, or one VLM request per candidate.

---

### Task 1: Typed VLM call policy

**Files:**
- Create: `src/camera_ai/vlm_policy.py`
- Create: `tests/test_vlm_policy.py`

**Interfaces:**
- Consumes: `VideoWindowObservation`, `Sequence[CandidateEvent]`, and `usable_frame_count: int`.
- Produces: `VLMCallDecision` and `decide_vlm_call(observation, candidates, *, usable_frame_count)`.

- [ ] **Step 1: Write the failing policy tests**

```python
from camera_ai.event_models import CandidateEvent, Priority
from camera_ai.schemas import MotionResult, VideoWindowObservation
from camera_ai.vlm_policy import VLMCallReason, decide_vlm_call


def observation(*, motion=False):
    return VideoWindowObservation(
        camera_id="cam",
        window_id="cam_000001",
        start_ms=0,
        end_ms=5000,
        motion=MotionResult(motion=motion, score=0.8 if motion else 0.0),
    )


def test_static_window_skips_vlm():
    result = decide_vlm_call(observation(), [], usable_frame_count=2)
    assert result.call_vlm is False
    assert result.reason is VLMCallReason.STATIC_WINDOW
    assert result.candidate_id is None


def test_candidate_calls_vlm_once_for_primary_candidate():
    candidate = CandidateEvent(
        candidate_id="candidate-1",
        window_id="cam_000001",
        candidate_type="unknown_motion",
        priority=Priority.LOW,
    )
    result = decide_vlm_call(observation(motion=True), [candidate], usable_frame_count=2)
    assert result.call_vlm is True
    assert result.reason is VLMCallReason.CANDIDATE_REQUIRES_VERIFICATION
    assert result.candidate_id == "candidate-1"


def test_no_usable_frames_skips_before_candidate_verification():
    candidate = CandidateEvent(
        candidate_id="candidate-1",
        window_id="cam_000001",
        candidate_type="person_only_activity",
    )
    result = decide_vlm_call(observation(motion=True), [candidate], usable_frame_count=0)
    assert result.call_vlm is False
    assert result.reason is VLMCallReason.NO_USABLE_FRAMES
```

- [ ] **Step 2: Run the tests and verify the missing module failure**

Run: `pytest tests/test_vlm_policy.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'camera_ai.vlm_policy'`.

- [ ] **Step 3: Implement the minimal deterministic policy**

```python
from enum import Enum
from typing import Sequence

from pydantic import BaseModel

from .event_models import CandidateEvent, Priority, select_primary_candidate
from .schemas import VideoWindowObservation


class VLMCallReason(str, Enum):
    STATIC_WINDOW = "static_window"
    NO_USABLE_FRAMES = "no_usable_frames"
    CANDIDATE_REQUIRES_VERIFICATION = "candidate_requires_verification"


class VLMCallDecision(BaseModel):
    call_vlm: bool
    reason: VLMCallReason
    priority: Priority = Priority.LOW
    candidate_id: str | None = None


def decide_vlm_call(
    observation: VideoWindowObservation,
    candidates: Sequence[CandidateEvent],
    *,
    usable_frame_count: int,
) -> VLMCallDecision:
    primary = select_primary_candidate(candidates)
    if usable_frame_count == 0:
        return VLMCallDecision(call_vlm=False, reason=VLMCallReason.NO_USABLE_FRAMES)
    if primary is None:
        return VLMCallDecision(call_vlm=False, reason=VLMCallReason.STATIC_WINDOW)
    return VLMCallDecision(
        call_vlm=True,
        reason=VLMCallReason.CANDIDATE_REQUIRES_VERIFICATION,
        priority=primary.priority,
        candidate_id=primary.candidate_id,
    )
```

- [ ] **Step 4: Run policy tests**

Run: `pytest tests/test_vlm_policy.py -q`

Expected: 3 tests pass.

- [ ] **Step 5: Commit the policy boundary**

```powershell
git add src/camera_ai/vlm_policy.py tests/test_vlm_policy.py
git commit -m "feat: add conservative vlm call policy"
```

### Task 2: Gate Qwen inside `VideoWindowProcessor`

**Files:**
- Modify: `src/camera_ai/video_windows.py`
- Modify: `tests/test_pipeline_events.py`

**Interfaces:**
- Consumes: `decide_vlm_call()` from Task 1 and the existing primary candidate selected by `select_primary_candidate()`.
- Produces: `ProcessedVideoWindow.vlm_call: VLMCallDecision`; skipped windows have no `ModelDecision` or `AlertEvent`.

- [ ] **Step 1: Add failing processor tests**

Add a detector returning no detections and a truly static window, then assert:

```python
class EmptyDetector:
    def detect(self, frame):
        return []


def static_window():
    frame = np.zeros((32, 32, 3), dtype=np.uint8)
    return RawVideoWindow(
        window_index=0,
        start_seconds=0,
        observations=[
            VideoFrameObservation(frame_index=0, timestamp_seconds=0, frame=frame),
            VideoFrameObservation(frame_index=5, timestamp_seconds=1, frame=frame.copy()),
        ],
    )


def test_static_window_skips_vlm_and_returns_green_result():
    vlm = VLM()
    result = VideoWindowProcessor(
        detector=EmptyDetector(), vlm=vlm, yolo_fps=2, max_keyframes=2
    ).process(static_window(), camera_id="cam_static")

    assert vlm.calls == 0
    assert result.vlm_call.call_vlm is False
    assert result.vlm_call.reason.value == "static_window"
    assert result.scene.alert_level.value == "low"
    assert result.scene.degraded is False
    assert result.timing.qwen_ms == 0
    assert result.decision is None
    assert result.alert_event is None
```

Also extend the existing person and fire tests with:

```python
assert result.vlm_call.call_vlm is True
assert result.vlm_call.candidate_id == result.decision.candidate_id
assert vlm.calls == 1
```

- [ ] **Step 2: Run focused processor tests and verify failure**

Run: `pytest tests/test_pipeline_events.py -q`

Expected: static test fails because VLM is called and `ProcessedVideoWindow` has no `vlm_call` field.

- [ ] **Step 3: Integrate the policy without duplicating routing**

In `ProcessedVideoWindow`, add:

```python
vlm_call: VLMCallDecision
```

After `routing_primary` is selected, calculate:

```python
vlm_call = decide_vlm_call(
    routing_observation,
    routing_candidates,
    usable_frame_count=len(frames),
)
```

Before the existing trace-capable VLM branch, add the skip branch:

```python
if not vlm_call.call_vlm:
    no_frames = vlm_call.reason is VLMCallReason.NO_USABLE_FRAMES
    scene = SceneAnalysis(
        summary=(
            "Không có frame hợp lệ để phân tích."
            if no_frames
            else "Không phát hiện chuyển động hoặc đối tượng cần xác minh."
        ),
        alert_level=AlertLevel.LOW,
        degraded=no_frames,
    )
    trace = VLMAnalysisTrace(scene=scene)
elif hasattr(self.vlm, "analyze_with_trace"):
    # retain the existing one-call candidate-aware path
```

Only create `decision` and `alert` when `vlm_call.call_vlm` is true. Include
`vlm_call=vlm_call` in the returned model and keep the artifact writer call so
the policy metadata can be inspected even when prompt/raw output are empty.

- [ ] **Step 4: Run processor and policy tests**

Run: `pytest tests/test_vlm_policy.py tests/test_pipeline_events.py -q`

Expected: all focused tests pass and every candidate path still calls VLM once.

- [ ] **Step 5: Commit processor integration**

```powershell
git add src/camera_ai/video_windows.py tests/test_pipeline_events.py
git commit -m "feat: skip qwen for static video windows"
```

### Task 3: Persist skipped status and policy reason

**Files:**
- Modify: `src/camera_ai/analysis_store.py`
- Modify: `tests/test_analysis_store.py`

**Interfaces:**
- Consumes: `ProcessedVideoWindow.vlm_call` from Task 2.
- Produces: `VideoWindowResult.vlm.status` equal to `skipped` or `completed`, plus `event_metadata.vlm_call`.

- [ ] **Step 1: Write failing persistence tests**

Build one processed static result through `VideoWindowProcessor`, append its
pending `VideoWindowResult`, complete it, and assert:

```python
saved = (await store.get("analysis-1")).windows[0]
assert saved.vlm.status == "skipped"
assert saved.vlm.skipped is True
assert saved.event_metadata["vlm_call"] == {
    "call_vlm": False,
    "reason": "static_window",
    "priority": "low",
    "candidate_id": None,
}
assert saved.timing.qwen_ms == 0
assert (await store.get("analysis-1")).status == "completed"
```

Add a called-window assertion that status remains `completed` and `skipped` is
false.

- [ ] **Step 2: Run the focused store tests**

Run: `pytest tests/test_analysis_store.py -q`

Expected: skipped-window assertions fail because the store always writes `completed`.

- [ ] **Step 3: Project the typed policy additively**

In `complete_processed_window`, derive status once:

```python
vlm_skipped = not processed.vlm_call.call_vlm
vlm_status = "skipped" if vlm_skipped else "completed"
```

Create `VLMResult` with both `status=vlm_status` and `skipped=vlm_skipped`. Add:

```python
"vlm_call": processed.vlm_call.model_dump(mode="json"),
```

to `event_metadata`. Update `_refresh_status()` so a producer-finished analysis
is complete when every window status belongs to `{"completed", "skipped"}`.

- [ ] **Step 4: Run persistence and async pipeline tests**

Run: `pytest tests/test_analysis_store.py tests/test_async_video_pipeline.py tests/test_pipeline_events.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit persistence changes**

```powershell
git add src/camera_ai/analysis_store.py tests/test_analysis_store.py
git commit -m "feat: persist vlm skip decisions"
```

### Task 4: Add per-video call-rate and five-second SLO reporting

**Files:**
- Modify: `scripts/benchmark_pipeline.py`
- Modify: `src/camera_ai/benchmark.py`
- Modify: `tests/test_benchmark_cli.py`
- Modify: `tests/test_benchmark_metrics.py`

**Interfaces:**
- Consumes: `window.event_metadata.vlm_call` and `window.timing.total_ms`.
- Produces: per-window `vlm_call`, `within_processing_budget`; per-video `performance_summary`; aggregate `BenchmarkSummary` SLO fields.

- [ ] **Step 1: Add failing per-video report test**

Extend a two-window payload with one skipped and one called policy decision:

```python
payload = analysis_payload("low", "medium")
payload["windows"][0]["timing"]["total_ms"] = 100
payload["windows"][0]["event_metadata"] = {
    "vlm_call": {"call_vlm": False, "reason": "static_window"}
}
payload["windows"][1]["timing"]["total_ms"] = 5200
payload["windows"][1]["event_metadata"] = {
    "vlm_call": {"call_vlm": True, "reason": "candidate_requires_verification"}
}

report = build_video_report(tmp_path / "mixed.mp4", "analysis-4", payload)
assert report["performance_summary"] == {
    "vlm_called_windows": 1,
    "vlm_skipped_windows": 1,
    "vlm_call_rate": 0.5,
    "processing_p95_ms": 5200.0,
    "windows_over_budget": 1,
    "processing_budget_ms": 5000.0,
}
assert report["windows"][0]["within_processing_budget"] is True
assert report["windows"][1]["within_processing_budget"] is False
```

- [ ] **Step 2: Add failing aggregate metric test**

Create observations with latencies 100 and 5,200 ms, and VLM calls 0 and 1,
then assert:

```python
assert summary.vlm_called_windows == 1
assert summary.vlm_skipped_windows == 1
assert summary.vlm_call_rate == 0.5
assert summary.processing_p95_ms == 5200.0
assert summary.windows_over_budget == 1
```

- [ ] **Step 3: Run benchmark tests and verify missing metrics**

Run: `pytest tests/test_benchmark_cli.py tests/test_benchmark_metrics.py -q`

Expected: assertions fail because performance fields do not exist.

- [ ] **Step 4: Implement deterministic nearest-rank p95 and counters**

Add a shared helper to `camera_ai.benchmark`:

```python
PROCESSING_BUDGET_MS = 5000.0


def nearest_rank_percentile(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(1, math.ceil(percentile * len(ordered)))
    return float(ordered[rank - 1])
```

Extend `BenchmarkSummary` with defaults for `vlm_called_windows`,
`vlm_skipped_windows`, `vlm_call_rate`, `processing_p95_ms`, and
`windows_over_budget`. Aggregate `vlm_calls > 0` as called and zero as skipped.

In `build_video_report`, retain `vlm_call` on every window, calculate
`within_processing_budget = total_ms <= PROCESSING_BUDGET_MS`, and calculate the
same counters plus nearest-rank p95 for `performance_summary`. If old API output
has no `vlm_call`, infer called/skipped from `window.vlm.skipped` so older payloads
remain readable.

- [ ] **Step 5: Run benchmark tests**

Run: `pytest tests/test_benchmark_cli.py tests/test_benchmark_metrics.py -q`

Expected: all benchmark tests pass.

- [ ] **Step 6: Commit benchmark observability**

```powershell
git add scripts/benchmark_pipeline.py src/camera_ai/benchmark.py tests/test_benchmark_cli.py tests/test_benchmark_metrics.py
git commit -m "feat: report qwen call rate and window slo"
```

### Task 5: Document, regress, and prepare the real benchmark command

**Files:**
- Modify: `docs/camera-ai-pipeline.md`
- Modify: `docs/benchmarks/README.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: CLI and JSON fields implemented in Tasks 1–4.
- Produces: operator documentation and verification evidence; no new runtime API.

- [ ] **Step 1: Document the conservative async gate**

Document the exact async order:

```text
Motion -> YOLO/specialized detector -> Router -> VLMCallPolicy
  static/no candidate -> skipped green window
  candidate -> one Qwen 4B call -> decision/alert
```

State that bbox remains internal, fire requires the optional specialized
detector plus temporal confirmation, and the report—not the implementation
alone—determines whether p95 meets 5 seconds.

- [ ] **Step 2: Document the single-video benchmark command and output fields**

```powershell
python -m scripts.benchmark_pipeline `
  --input-file "D:\CongViec\CameraAI\CameraAI\videos\RoadAccidents005_x264.mp4" `
  --output-dir "D:\CongViec\CameraAI\CameraAI\runs\alerts"
```

Describe `vlm_call`, `within_processing_budget`, and `performance_summary`.

- [ ] **Step 3: Run the focused feature suite**

Run: `pytest tests/test_vlm_policy.py tests/test_pipeline_events.py tests/test_analysis_store.py tests/test_benchmark_cli.py tests/test_benchmark_metrics.py -q`

Expected: all focused tests pass.

- [ ] **Step 4: Run the complete regression suite once**

Run: `pytest -q`

Expected: all tests pass; FastAPI `on_event` deprecation warnings are acceptable existing warnings.

- [ ] **Step 5: Check patch hygiene**

Run: `git diff --check`

Expected: no whitespace errors. Review `git status --short` and preserve unrelated user changes, including the untracked `--input-file` artifact.

- [ ] **Step 6: Commit documentation only if it is not already included**

```powershell
git add README.md docs/camera-ai-pipeline.md docs/benchmarks/README.md
git commit -m "docs: explain async qwen gate metrics"
```

- [ ] **Step 7: Run the real benchmark only when API and Ollama are available**

Start the existing API in a separate PowerShell process, then run the documented
single-video command. Inspect `performance_summary.processing_p95_ms` and
`windows_over_budget`. Do not represent the ≤5-second SLO as achieved until this
runtime measurement passes on the target machine.
