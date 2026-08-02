# Pre-Queue Red Admission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop known-red camera windows before Motion/Detection/Qwen and keep stale same-camera work out of the global queue.

**Architecture:** The producer asks the camera state store for an atomic pre-queue admission before it creates persistence records or queue tasks. A reserved recheck admission travels with its task, while a prunable condition-backed heap removes stale queued work when a red episode is created or extended. The analysis stores one cooldown summary for dropped work instead of one result per skipped window.

**Tech Stack:** Python 3.11+, asyncio, Pydantic, FastAPI, pytest/pytest-asyncio.

## Global Constraints

- Uploaded video uses source seconds; a future live adapter passes monotonic seconds through the same admission API.
- Do not add UI, an external broker, a database migration, or per-camera queues.
- Keep processor-side cooldown admission as defense in depth.
- Dropped and pruned windows must not appear in the public `windows` array.

---

### Task 1: Atomic producer admission and reserved recheck

**Files:**
- Modify: `src/camera_ai/alert_cooldown.py`
- Modify: `src/camera_ai/video_windows.py`
- Modify: `src/camera_ai/pipeline.py`
- Test: `tests/test_alert_cooldown.py`
- Test: `tests/test_video_windows.py`

**Interfaces:**
- Produces: `CameraAlertStateStore.admit_before_queue(..., processing_now: float) -> WindowAdmission`.
- Produces: `WindowAdmission.reservation_version: int | None` and `CameraAlertRuntime.recheck_reserved: bool`.
- Produces: `VideoWindowProcessor.process(..., admission: WindowAdmission | None = None)` and matching pipeline facade argument.

- [ ] **Step 1: Write failing state-store tests**

Add tests proving a normal stream is admitted even when an ordinary VLM is in flight, active red before its deadline is rejected, and two calls at/after the deadline return exactly one `PROCESS_RECHECK` admission with a non-null reservation version.

```python
first = store.admit_before_queue(stream_id="a", camera_id="cam", start_seconds=65, end_seconds=70, processing_now=1)
second = store.admit_before_queue(stream_id="a", camera_id="cam", start_seconds=70, end_seconds=75, processing_now=1)
assert first.process_window is True
assert first.reservation_version is not None
assert second.process_window is False
```

- [ ] **Step 2: Run the new state tests and verify RED**

Run: `$env:PYTHONPATH='src'; pytest tests/test_alert_cooldown.py -q`

Expected: FAIL because `admit_before_queue`, `recheck_reserved`, and `reservation_version` do not exist.

- [ ] **Step 3: Implement atomic producer admission**

Under the existing `RLock`, make normal state admission independent of `vlm_inflight`; only an active episode can be dropped. Reserve one due recheck by setting `recheck_reserved=True`, incrementing `state_version`, and returning that version. Clear the reservation from both `record_result` and `record_failure`. Keep `inspect_window` unchanged for the processor-side safety gate.

- [ ] **Step 4: Write and run a failing reserved-admission processor test**

Pass the reserved admission into `process_video_window` and assert it reaches the normal processing path instead of being returned as a suppressed zero-timing window.

Run: `$env:PYTHONPATH='src'; pytest tests/test_video_windows.py -q`

Expected: FAIL because the facade and processor do not accept `admission`.

- [ ] **Step 5: Thread the optional admission through processor and facade**

Use the supplied admission when present; otherwise call `inspect_window` exactly as today. This prevents a reserved recheck from suppressing itself while preserving defense-in-depth for normal queued tasks.

- [ ] **Step 6: Run focused tests and commit**

Run: `$env:PYTHONPATH='src'; pytest tests/test_alert_cooldown.py tests/test_video_windows.py -q`

Commit: `feat: reserve cooldown rechecks before queue`

### Task 2: Prunable priority queue

**Files:**
- Modify: `src/camera_ai/queue.py`
- Test: `tests/test_queue.py`

**Interfaces:**
- Consumes: `WindowAdmission` from Task 1 as `VLMTask.admission`.
- Produces: `VLMQueue.remove_where(predicate: Callable[[VLMTask], bool]) -> list[VLMTask]`.

- [ ] **Step 1: Write failing queue removal tests**

Enqueue tasks for two analyses/cameras, remove only matching source windows before a literal deadline, then assert the returned task IDs, exact depth, and dequeue order of the survivors.

```python
removed = await queue.remove_where(
    lambda task: task.analysis_id == "analysis-a"
    and task.camera_id == "cam-a"
    and task.raw_window is not None
    and task.raw_window.start_seconds < 65.0
)
assert [task.task_id for task in removed] == ["a-stale"]
assert queue.depth == 2
```

- [ ] **Step 2: Run queue tests and verify RED**

Run: `$env:PYTHONPATH='src'; pytest tests/test_queue.py -q`

Expected: FAIL because `remove_where` does not exist.

- [ ] **Step 3: Replace PriorityQueue wrapper with a condition-backed heap**

Store `list[VLMTask]` behind one `asyncio.Condition`. `enqueue` pushes and notifies, `dequeue` waits while empty and preserves the existing aging behavior, and `remove_where` partitions plus `heapq.heapify` while holding the same condition. Copy `admission` when rebuilding an aged task.

- [ ] **Step 4: Run focused queue tests and commit**

Run: `$env:PYTHONPATH='src'; pytest tests/test_queue.py -q`

Commit: `feat: prune stale camera tasks from vlm queue`

### Task 3: Cooldown summary and persistence cleanup

**Files:**
- Modify: `src/camera_ai/analysis_store.py`
- Modify: `src/camera_ai/alert_store.py`
- Test: `tests/test_analysis_store.py`
- Test: `tests/test_alert_store.py`

**Interfaces:**
- Produces: `CooldownSummary(active_alert_id, alert_level, timebase, red_started, recheck_at, suppressed_windows, suppressed_seconds)` on `VideoAnalysis` and `CompactVideoAnalysis`.
- Produces: `AnalysisStore.record_suppressed(...)`, `AnalysisStore.remove_pending_windows(...) -> list[str]`, and `AlertStore.delete(alert_id: str)`.

- [ ] **Step 1: Write failing summary and cleanup tests**

Assert that recording one dropped 5-second window creates/updates the additive summary without appending a window. Append two pending windows, remove one by alert ID, and assert only the other remains and totals/status refresh. Assert deleting an alert makes `get` return `None`.

- [ ] **Step 2: Run persistence tests and verify RED**

Run: `$env:PYTHONPATH='src'; pytest tests/test_analysis_store.py tests/test_alert_store.py -q`

Expected: FAIL because the summary and cleanup methods do not exist.

- [ ] **Step 3: Implement compact summary and cleanup methods**

Use a typed `Literal["video", "monotonic"]` timebase. `record_suppressed` adds one or more literal window durations and updates the current alert/deadline fields. `remove_pending_windows` removes only matching `status == "pending"` records, counts their source durations in the same summary, refreshes totals/status, and returns removed alert IDs. Add an idempotent in-memory alert delete.

- [ ] **Step 4: Run persistence tests and commit**

Run: `$env:PYTHONPATH='src'; pytest tests/test_analysis_store.py tests/test_alert_store.py -q`

Commit: `feat: summarize prequeue cooldown suppression`

### Task 4: Producer admission and worker pruning integration

**Files:**
- Modify: `apps/api/main.py`
- Modify: `src/camera_ai/queue.py`
- Test: `tests/test_async_video_response.py`
- Test: `tests/test_queue.py`

**Interfaces:**
- Consumes: Tasks 1-3 interfaces.
- Producer `_enqueue_video_window` returns without creating an alert/window/task for rejected admissions.
- Worker prunes matching backlog when `episode_created` or `episode_extended` is true.

- [ ] **Step 1: Write failing producer integration tests**

Seed red state, submit a pre-deadline raw window, and assert real queue depth, stored windows, and compatibility alerts remain zero while `cooldown.suppressed_windows == 1`. Submit two due windows and assert only one pending task exists.

- [ ] **Step 2: Run producer tests and verify RED**

Run: `$env:PYTHONPATH='src'; pytest tests/test_async_video_response.py -q`

Expected: FAIL because every raw window is currently persisted and enqueued.

- [ ] **Step 3: Apply producer-side admission**

Call `admit_before_queue` before UUID creation using `window.start_seconds`, `window.end_seconds`, and `time.monotonic()`. Rejected admissions call `record_suppressed(..., timebase="video")` only. Admitted rechecks carry their admission on `VLMTask`.

- [ ] **Step 4: Write failing worker pruning test**

Process a task whose real `ProcessedVideoWindow.alert_context.episode_created` is true while same-analysis stale work and another-camera work are queued. Assert the stale work disappears from queue, analysis windows, and alert store; assert the other camera remains.

- [ ] **Step 5: Run worker test and verify RED**

Run: `$env:PYTHONPATH='src'; pytest tests/test_queue.py -q`

Expected: FAIL because the worker does not prune after red confirmation.

- [ ] **Step 6: Implement worker pruning and cleanup**

After persisting the processed red window, remove queued tasks matching `(analysis_id, camera_id)` and `raw_window.start_seconds < next_recheck_event_seconds`. Pass their IDs/durations to `remove_pending_windows`, delete returned compatibility alerts, and leave the currently executing task untouched.

- [ ] **Step 7: Run integration tests and commit**

Run: `$env:PYTHONPATH='src'; pytest tests/test_async_video_response.py tests/test_queue.py -q`

Commit: `feat: stop red camera work before global queue`

### Task 5: Benchmark contract and full verification

**Files:**
- Modify: benchmark serializer discovered by `rg -n "cooldown_suppression|vlm_skipped_windows" .`
- Modify: `tests/test_benchmark_cli.py`
- Modify: `README.md` if its output example contains per-window suppressed records.

**Interfaces:**
- Consumes: top-level compact analysis cooldown summary from Task 3.
- Produces: benchmark JSON with one top-level `cooldown` object and suppression totals derived from processed plus dropped windows.

- [ ] **Step 1: Write a failing benchmark serialization test**

Use a fixture with two processed windows and ten dropped windows. Assert `cooldown.suppressed_windows == 10`, `vlm_skipped_windows` includes the ten dropped windows, and no synthetic suppressed entries appear in `windows`.

- [ ] **Step 2: Run benchmark test and verify RED**

Run: `$env:PYTHONPATH='src'; pytest tests/test_benchmark_cli.py -q`

- [ ] **Step 3: Update serializer and documentation**

Serialize the typed top-level cooldown summary and calculate the suppression denominator as `len(processed_windows) + suppressed_windows`. Keep existing p50/p95 latency fields based only on work that actually ran.

- [ ] **Step 4: Run all verification**

Run:

```powershell
$env:PYTHONPATH='src'
pytest -q
git diff --check
```

Expected: all tests pass and `git diff --check` prints nothing.

- [ ] **Step 5: Commit**

Commit: `refactor: report dropped cooldown windows compactly`
