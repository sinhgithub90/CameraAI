# Camera AI Architecture Foundation and Event Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve the current Motion → YOLO26n → two-keyframe → Qwen pipeline into a measurable event-analysis architecture with explicit observation, candidate, decision, alert, tracking, spatial rules, and gated model routing.

**Architecture:** Preserve the existing synchronous `SecurityAIPipeline` and `PipelineResult` as the compatibility boundary. Add explicit data contracts and orchestration stages incrementally: baseline/observability, window observation, Event Router, model decisions, specialized detector validation, independent tracking, then Zone/Line and traffic rules.

**Tech Stack:** Python 3.11+, Pydantic, OpenCV, Ultralytics YOLO, Ollama HTTP API, pytest, existing FastAPI adapter.

## Global Constraints

- Keep `window_seconds=5.0`, `motion_fps=5.0`, `yolo_fps=2.0`, `max_keyframes=2`, and `OLLAMA_FRAME_MODE=composite` as baseline defaults.
- Do not productionize Fast 2B → Strong 4B until an offline benchmark proves latency and quality benefit.
- Do not use VLM self-reported confidence as a hard escalation threshold in the first implementation.
- Candidate types describe hypotheses; only ModelDecision/policy may produce an AlertEvent.
- Keep `PipelineResult`, Ollama degraded fallback, and the FastAPI adapter backward compatible.
- Do not treat the existing orange/yellow color heuristic as a fire conclusion.
- Keep ByteTrack independent from Zone/Line and traffic Rule Engine.
- Every new stage must have unit tests and a reproducible log or artifact output.

## Current implementation snapshot (2026-08-01)

Đây là trạng thái đã có, không cần làm lại trong các task bên dưới:

- Async video đã có `VideoAnalysis` aggregate và endpoint
  `GET /analyses/{analysis_id}`; FE poll endpoint này, hiển thị timing tổng và
  card VLM mỗi window, không hiển thị detection table.
- Upload video đang mô phỏng stream segment: 0–5 giây được enqueue ngay, các
  segment tiếp theo phát theo nhịp 5 giây source-time.
- Có một queue/worker toàn cục. Video task chứa raw sampled observations; một
  worker chạy tuần tự Motion → YOLO (2 FPS) → keyframe (≤2) → Qwen. Image task
  vẫn là VLM-only trên cùng worker.
- `AnalysisStore` ghi lifecycle `reading → queued → completed/failed` và cộng
  Motion/YOLO/VLM timing theo window.
- Qwen prompt và parser đã xử lý response JSON rỗng bằng degraded fallback;
  FE có fallback để không render `VLM: —` cho result completed.

### Gap cần xử lý trước roadmap domain

- Đổi tên/refactor `VLMQueue`/`VLMWorker` thành abstraction trung tính như
  `WindowPipelineQueue`/`WindowPipelineWorker`, hoặc giữ alias tương thích;
  hiện tên không phản ánh video task chạy toàn pipeline.
- Tách `processed: dict` trong `AnalysisStore.complete_processed_window()`
  thành Pydantic result contract có type rõ ràng.
- Bổ sung integration test dùng video source thật cho: segment 0 enqueue ngay,
  segment 1 không phát trước 5 giây, và kết quả VLM window đi qua
  `/analyses/{id}`.
- Thêm metric wall-clock per-window (`queued_at`, `started_at`, `completed_at`)
  tách khỏi tổng stage time; tổng stage time không biểu diễn thứ tự thực thi.

---

## File map

- Modify `src/camera_ai/schemas.py`: add window-level observation and event contracts while retaining existing response models.
- Keep `src/camera_ai/events.py`: system EventBus messages (`Event`, `EventBus`, `InProcessEventBus`). Do not place domain contracts here.
- Create `src/camera_ai/event_models.py`: CandidateEvent, ModelDecision, AlertEvent, policy, and compatibility projection helpers.
- Create `src/camera_ai/router.py`: deterministic Event Router over observation evidence.
- Modify `src/camera_ai/pipeline.py`: orchestrate the new stages behind the existing public method.
- Create `src/camera_ai/benchmark.py`: serializable benchmark case/result models and metric aggregation.
- Create `scripts/benchmark_pipeline.py`: offline runner for fixed media cases and model/keyframe comparisons.
- Create `docs/benchmarks/README.md`: fixture manifest, annotation format, and repeatable commands.
- Create `tests/test_event_contracts.py`, `tests/test_event_router.py`, and `tests/test_benchmark_metrics.py` for the foundation.
- Create `src/camera_ai/tracking.py` and `tests/test_tracking.py` only after the event foundation is merged.
- Create `src/camera_ai/spatial_rules.py` and `tests/test_spatial_rules.py` after tracking is validated.
- Create `src/camera_ai/traffic_rules.py` and `tests/test_traffic_rules.py` after Zone/Line.

### Task 1: Capture the baseline benchmark contract

**Files:**
- Create: `docs/benchmarks/README.md`
- Create: `src/camera_ai/benchmark.py`
- Create: `tests/test_benchmark_metrics.py`

**Interfaces:**
- Consumes: existing `PipelineResult`, `VideoAnalysisStats`, `VideoWindowResult`.
- Produces: `BenchmarkCase`, `BenchmarkObservation`, `BenchmarkSummary`, and a JSON-serializable metrics report.

- [ ] **Step 1: Write failing metric tests**

```python
def test_benchmark_summary_aggregates_latency_and_vlm_calls():
    summary = summarize_benchmark([
        BenchmarkObservation(case_id="normal_01", expected_event="normal", latency_ms=100, vlm_calls=0, predicted_event="normal"),
        BenchmarkObservation(case_id="fall_01", expected_event="fall", latency_ms=300, vlm_calls=1, predicted_event="fall"),
    ])
    assert summary.case_count == 2
    assert summary.mean_latency_ms == 200
    assert summary.recall_by_event["fall"] == 1.0
    assert summary.vlm_calls_total == 1
```

- [ ] **Step 2: Run `pytest tests/test_benchmark_metrics.py -q` and verify it fails because the benchmark types/functions do not exist.**
- [ ] **Step 3: Implement Pydantic models and deterministic aggregation in `src/camera_ai/benchmark.py`.**
- [ ] **Step 4: Add `docs/benchmarks/README.md` with normal, accident, fall, fighting, fire/smoke, hard-negative, and tamper categories; document JSON annotations without requiring video files in the repository.**
- [ ] **Step 5: Run `pytest tests/test_benchmark_metrics.py -q`; expected result: all tests pass.**
- [ ] **Step 6: Commit with `git add src/camera_ai/benchmark.py tests/test_benchmark_metrics.py docs/benchmarks/README.md && git commit -m "test: define camera ai benchmark baseline"`.**

### Task 2: Add explicit observation, candidate, decision, and alert contracts

**Files:**
- Modify: `src/camera_ai/schemas.py`
- Create: `src/camera_ai/event_models.py`
- Create: `tests/test_event_contracts.py`

**Interfaces:**
- Consumes: `EventObject`, `MotionResult`, `Detection`, `VideoFrameObservation`, `VideoWindowResult`.
- Produces: `VideoWindowObservation`, `CandidateEvent`, `ModelDecision`, `AlertEvent`, `DecisionValue`, `Priority`, and `project_alert_to_pipeline_result(...)` from `src/camera_ai/event_models.py`.

- [ ] **Step 1: Write failing schema tests**

```python
def test_candidate_is_hypothesis_not_alert():
    candidate = CandidateEvent(
        candidate_id="candidate_001",
        window_id="cam_01_000001",
        candidate_type="possible_person_vehicle_interaction",
        priority="medium",
        requires_verification=True,
    )
    assert candidate.candidate_type.startswith("possible_")
    assert candidate.requires_verification is True

def test_invalid_model_decision_value_is_rejected():
    with pytest.raises(ValidationError):
        ModelDecision(candidate_id="c1", model="qwen", decision="maybe")
```

- [ ] **Step 2: Run `pytest tests/test_event_contracts.py -q` and verify it fails.**
- [ ] **Step 3: Add strict enums for decision, priority, severity, and alert status; include `raw_output_valid`, optional confidence for analysis, evidence, and latency in `src/camera_ai/event_models.py`.**
- [ ] **Step 4: Implement stable IDs from request/window context and project the highest-priority verified result into existing `SecurityDecision`/`VLMResult` fields.**
- [ ] **Step 5: Add an explicit adapter from domain `AlertEvent` to the existing `alert_store.Alert`; preserve `VLMResult.status` as `pending`, `completed`, or `skipped`.**
- [ ] **Step 6: Run the new tests plus `pytest tests/test_video_pipeline.py -q`; expected result: all pass and existing schemas remain valid.**
- [ ] **Step 7: Commit with `git add src/camera_ai/schemas.py src/camera_ai/event_models.py tests/test_event_contracts.py && git commit -m "feat: add camera event data contracts"`.**

### Task 3: Build the deterministic Event Router

**Files:**
- Create: `src/camera_ai/router.py`
- Create: `tests/test_event_router.py`

**Interfaces:**
- Consumes: `VideoWindowObservation` and motion/detection evidence.
- Produces: `list[CandidateEvent]` from `route_observation(observation: VideoWindowObservation) -> list[CandidateEvent]`.

- [ ] **Step 1: Write failing routing tests**

```python
def test_person_and_vehicle_motion_routes_to_interaction_candidate(observation):
    candidates = route_observation(observation)
    assert [c.candidate_type for c in candidates] == ["possible_person_vehicle_interaction"]
    assert candidates[0].requires_verification is True

def test_motion_without_objects_routes_to_unknown_motion(observation_without_detections):
    candidates = route_observation(observation_without_detections)
    assert candidates[0].candidate_type == "unknown_motion"
```

- [ ] **Step 2: Run `pytest tests/test_event_router.py -q` and verify it fails.**
- [ ] **Step 3: Implement conservative rules using class presence, count, bbox proximity, motion peak, and temporal change; never emit final names such as `traffic_accident` or `fighting`.**
- [ ] **Step 4: Add tests for person-only activity, vehicle-only activity, multiple-person high motion, possible fire visual change, and camera tamper evidence.**
- [ ] **Step 5: Run `pytest tests/test_event_router.py tests/test_event_contracts.py -q`; expected result: all pass.**
- [ ] **Step 6: Commit with `git add src/camera_ai/router.py tests/test_event_router.py && git commit -m "feat: add conservative event router"`.**

### Task 4: Integrate observations and candidate artifacts without changing detection behavior

**Files:**
- Modify: `src/camera_ai/pipeline.py`
- Modify: `src/camera_ai/schemas.py`
- Modify: `tests/test_video_pipeline.py`
- Create: `tests/test_pipeline_events.py`

**Interfaces:**
- Consumes: current `VideoFrameObservation` list, `select_keyframes`, `route_observation`, and the existing async `VLMTask`/`AlertStore` lifecycle.
- Produces: one `VideoWindowObservation` and zero or more `CandidateEvent` records per analyzed window; existing `PipelineResult` remains populated and async results retain `pending → completed/skipped` status.

- [ ] **Step 1: Write failing integration tests asserting that a synthetic motion window exposes observation/candidate metadata while Qwen call count remains unchanged.**
- [ ] **Step 2: Run `pytest tests/test_pipeline_events.py tests/test_video_pipeline.py -q` and verify the new assertions fail.**
- [ ] **Step 3: Build window observations from existing sampled observations; do not change sampling intervals or the default two-keyframe selector.**
- [ ] **Step 4: Call the router once per motion window and attach serializable event metadata through an additive optional field or sidecar result, preserving current API fields. For async requests, create/update the existing `alert_store.Alert`, enqueue `VLMTask`, and let `VLMWorker` call `pipeline.analyze_vlm()` rather than creating a second worker path.**
- [ ] **Step 5: Add opt-in artifact writing controlled by an explicit output directory argument; write JSON and selected JPEGs only when enabled.**
- [ ] **Step 6: Add integration assertions for `AlertStore.Alert.vlm.status` and `VLMQueue` enqueue/update behavior; keep synchronous `analyze_event()` unchanged.**
- [ ] **Step 7: Run `pytest -q`; expected result: existing behavior and new event assertions pass.**
- [ ] **Step 8: Commit with `git add src/camera_ai/pipeline.py src/camera_ai/schemas.py tests/test_video_pipeline.py tests/test_pipeline_events.py && git commit -m "feat: expose window observations and candidates"`.**

### Task 5: Add the offline benchmark runner and baseline report

**Files:**
- Create: `scripts/benchmark_pipeline.py`
- Modify: `docs/benchmarks/README.md`
- Create: `tests/test_benchmark_cli.py`

**Interfaces:**
- Consumes: a manifest JSON, local media paths, `SecurityAIPipeline`, and optional `--model`/`--frame-mode` settings; benchmark both synchronous `analyze_event()` and async `detect()` plus worker completion when requested.
- Produces: one JSONL per case and one aggregate JSON report containing latency, VLM calls, JSON validity, predicted event, and expected event.

- [ ] **Step 1: Write a CLI test using a temporary manifest and a mocked pipeline; assert one result line per case and one summary object.**
- [ ] **Step 2: Run `pytest tests/test_benchmark_cli.py -q` and verify it fails because the script does not exist.**
- [ ] **Step 3: Implement arguments `--manifest`, `--output`, `--model`, `--frame-mode`, `--mode` (`sync` or `async`), and `--warmup`; use the same media for every compared configuration.**
- [ ] **Step 4: Document warm-up, cold-run exclusion, Ollama availability, and required case manifest fields.**
- [ ] **Step 5: Run the CLI test and a dry run against a small local fixture; expected result: JSONL and summary JSON are created.**
- [ ] **Step 6: Commit with `git add scripts/benchmark_pipeline.py tests/test_benchmark_cli.py docs/benchmarks/README.md && git commit -m "feat: add reproducible pipeline benchmark runner"`.**

### Task 6: Benchmark keyframe modes and Fast 2B versus Strong 4B

**Files:**
- Modify: `scripts/benchmark_pipeline.py`
- Modify: `src/camera_ai/vlm/ollama_qwen.py`
- Create: `tests/test_model_benchmark_config.py`
- Modify: `docs/benchmarks/README.md`

**Interfaces:**
- Consumes: benchmark runner and Qwen adapter configuration.
- Produces: comparable reports for 2 composite, 2 separate, 4 separate, and 6 separate frames, plus 2B/4B latency and resource observations.

- [ ] **Step 1: Write configuration tests that reject composite mode for frame counts other than two and accept separate mode for multi-frame input.**
- [ ] **Step 2: Run `pytest tests/test_model_benchmark_config.py -q` and verify the new coverage fails where validation is insufficient.**
- [ ] **Step 3: Add explicit benchmark configuration objects without changing production defaults or prompt semantics.**
- [ ] **Step 4: Run the same candidate manifest through Qwen 2B and 4B after warm-up; record JSON validity, latency, RAM/VRAM, model-switch cost, agreement, recall, and false-positive rate.**
- [ ] **Step 5: Write the decision in `docs/benchmarks/README.md`: cascade, single 4B, or separate Ollama instances; do not enable a cascade without measured benefit.**
- [ ] **Step 6: Commit with `git add scripts/benchmark_pipeline.py src/camera_ai/vlm/ollama_qwen.py tests/test_model_benchmark_config.py docs/benchmarks/README.md && git commit -m "test: compare qwen models and keyframe modes"`.**

### Task 7: Integrate specialized Fire/Smoke detection with temporal validation

**Files:**
- Modify: `src/camera_ai/detectors/fire.py`
- Modify: `src/camera_ai/pipeline.py`
- Create: `src/camera_ai/temporal_validation.py`
- Create: `tests/test_temporal_validation.py`
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: fire detector outputs per sampled frame.
- Produces: `TemporalSignal` and fire candidates; no direct high alert from one color-heuristic frame.

- [ ] **Step 1: Write failing tests requiring three consecutive positive observations before a fire candidate is emitted and requiring reset after a configured gap.**
- [ ] **Step 2: Run `pytest tests/test_temporal_validation.py -q` and verify it fails.**
- [ ] **Step 3: Implement the temporal validator with explicit `min_consecutive=3` and `max_gap_frames=1` defaults; keep detector confidence as evidence only.**
- [ ] **Step 4: Ensure the heuristic backend is labeled `heuristic` and cannot produce a final `AlertEvent` without temporal confirmation and policy review.**
- [ ] **Step 5: Add hard-negative metadata for lamps, sunlight, vehicle lights, orange clothing, welding, steam, and fog.**
- [ ] **Step 6: Run fire/pipeline tests and the full suite; expected result: existing runtime does not instantiate FireDetector by default and the opt-in path is covered.**
- [ ] **Step 7: Commit with `git add src/camera_ai/detectors/fire.py src/camera_ai/pipeline.py src/camera_ai/temporal_validation.py tests/test_temporal_validation.py tests/test_pipeline.py && git commit -m "feat: add temporally validated fire candidates"`.**

### Task 8: Add independent minimal tracking

**Files:**
- Create: `src/camera_ai/tracking.py`
- Create: `tests/test_tracking.py`
- Modify: `src/camera_ai/schemas.py`

**Interfaces:**
- Consumes: detections grouped by frame.
- Produces: `TrackState` with `track_id`, class, `first_seen`, `last_seen`, `age_frames`, `bbox_history`, and `center_history`.

- [ ] **Step 1: Write failing tests for stable IDs across nearby detections, new IDs after a missed-track timeout, and separate IDs for crossing classes.**
- [ ] **Step 2: Run `pytest tests/test_tracking.py -q` and verify it fails.**
- [ ] **Step 3: Add the smallest supported tracker interface; use ByteTrack only behind that interface and keep a deterministic fake/matcher for unit tests.**
- [ ] **Step 4: Record histories for 2–5 seconds without changing current `Detection.track_id` behavior unless tracking is explicitly enabled.**
- [ ] **Step 5: Add tests for occlusion, camera shake, low FPS, and object re-entry using synthetic detections.**
- [ ] **Step 6: Run `pytest tests/test_tracking.py tests/test_event_router.py -q`; expected result: tracking and routing remain independent.**
- [ ] **Step 7: Commit with `git add src/camera_ai/tracking.py src/camera_ai/schemas.py tests/test_tracking.py && git commit -m "feat: add opt-in minimal tracking"`.**

### Task 9: Add basic Zone/Line rules

**Files:**
- Create: `src/camera_ai/spatial_rules.py`
- Create: `tests/test_spatial_rules.py`
- Modify: `src/camera_ai/schemas.py`

**Interfaces:**
- Consumes: `TrackState`, polygon/line definitions, and frame timestamps.
- Produces: `SpatialEvent` for `zone_enter`, `zone_exit`, `line_crossing`, and `dwell_time`.

- [ ] **Step 1: Write failing geometry tests for enter, exit, line crossing direction, and dwell time greater than ten seconds.**
- [ ] **Step 2: Run `pytest tests/test_spatial_rules.py -q` and verify it fails.**
- [ ] **Step 3: Implement point-in-polygon using the bbox bottom-center anchor and line crossing using consecutive center points; require a prior point on the opposite side.**
- [ ] **Step 4: Suppress duplicate events for the same track/zone/line until the track leaves or crosses back.**
- [ ] **Step 5: Run spatial tests and the full suite; expected result: rules do not call VLM and do not depend on traffic semantics.**
- [ ] **Step 6: Commit with `git add src/camera_ai/spatial_rules.py src/camera_ai/schemas.py tests/test_spatial_rules.py && git commit -m "feat: add basic zone and line events"`.**

### Task 10: Add traffic Rule Engine candidates

**Files:**
- Create: `src/camera_ai/traffic_rules.py`
- Create: `tests/test_traffic_rules.py`
- Modify: `src/camera_ai/router.py`

**Interfaces:**
- Consumes: tracks, spatial events, recent motion, and vehicle trajectories.
- Produces: candidates for `vehicle_stop`, `wrong_way`, `congestion`, and `suspected_collision`; never emits a confirmed collision alert directly.

- [ ] **Step 1: Write failing tests for vehicle counting, prolonged stop, wrong-way direction, congestion, and collision-candidate evidence.**
- [ ] **Step 2: Run `pytest tests/test_traffic_rules.py -q` and verify it fails.**
- [ ] **Step 3: Implement rules in increasing complexity: line count, dwell-based stop, direction vector, density-plus-low-speed congestion, and proximity-plus-change collision suspicion.**
- [ ] **Step 4: Route `suspected_collision` to ModelDecision verification and require `yes` or an explicit review policy before creating a high AlertEvent.**
- [ ] **Step 5: Run traffic, spatial, tracking, and full regression tests.**
- [ ] **Step 6: Commit with `git add src/camera_ai/traffic_rules.py src/camera_ai/router.py tests/test_traffic_rules.py && git commit -m "feat: add traffic rule candidates"`.**

## Verification checklist

Before declaring the architecture implementation complete:

- Run `pytest -q` from `D:\CongViec\CameraAI\CameraAI`.
- Run the benchmark runner twice after warm-up on the same manifest.
- Verify the baseline still uses two keyframes and composite input.
- Verify a static video does not call YOLO or Qwen through the video path.
- Verify motion with no YOLO detection still calls Qwen once per window.
- Verify Ollama failure returns `degraded=true` and does not create a high alert.
- Verify async requests expose `pending` first and reach `completed` or `skipped` through the existing `VLMWorker` and `AlertStore`.
- Verify `events.py` remains the EventBus module and domain contracts live in `event_models.py`.
- Verify candidate names remain hypotheses and confirmed alert names only appear in policy output.
- Verify ByteTrack, Zone/Line, and traffic rules are opt-in and covered by isolated tests.
- Record benchmark results before selecting a 2B/4B cascade or changing keyframe count.
