# Camera AI Architecture Foundation and Event Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evolve the current Motion → YOLO26n → two-keyframe → Qwen pipeline into a measurable event-analysis architecture with explicit observation, candidate, decision, alert, tracking, spatial rules, and gated model routing.

**Architecture:** Preserve the existing synchronous `SecurityAIPipeline`, async `VLMQueue`/`VLMWorker`, and `PipelineResult` as compatibility boundaries. Add explicit, independently testable contracts and orchestration stages incrementally: baseline/observability, window observation, Event Router, model decisions, specialized detector validation, independent tracking, then Zone/Line and traffic rules. Keep execution in the current window worker until benchmark evidence justifies a later queue split.

**Tech Stack:** Python 3.11+, Pydantic, OpenCV, Ultralytics YOLO, Ollama HTTP API, pytest, existing FastAPI adapter.

## Global Constraints

- Keep `window_seconds=5.0`, `motion_fps=5.0`, `yolo_fps=2.0`, `max_keyframes=2`, and `OLLAMA_FRAME_MODE=composite` as baseline defaults.
- Use the default async video path as the only benchmark baseline. Keep sync for backward compatibility, but do not spend benchmark scope or roadmap gates comparing it with async.
- Do not productionize Fast 2B → Strong 4B until an offline benchmark proves latency and quality benefit.
- Do not use VLM self-reported confidence as a hard escalation threshold in the first implementation.
- Candidate types describe hypotheses; only ModelDecision/policy may produce an AlertEvent.
- Keep `PipelineResult`, Ollama degraded fallback, and the FastAPI adapter backward compatible.
- Do not treat the existing orange/yellow color heuristic as a fire conclusion.
- Keep ByteTrack independent from Zone/Line and traffic Rule Engine.
- Keep the current single window queue/worker during the foundation roadmap; do not add RabbitMQ, pub/sub, or multi-stage queues in these tasks.
- Make each new stage transport-neutral: define typed input/output contracts, deterministic behavior where applicable, and an adapter boundary before considering parallel workers.
- Preserve `analysis_id`, `window_index`, `alert_id`, and `VideoWindowResult` across any future execution split.
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
- Baseline metric contracts đã có trong `src/camera_ai/benchmark.py`, gồm
  `BenchmarkCase`, `BenchmarkObservation`, `BenchmarkSummary` và timing theo
  stage. Chưa có runner chạy manifest/media thật và chưa có báo cáo baseline.
- Các domain contract `VideoWindowObservation`, `CandidateEvent`,
  `ModelDecision`, `AlertEvent` cùng adapter tương thích đã có và có unit test;
  chúng chưa được nối vào runtime window pipeline.

### Gap cần xử lý trước roadmap domain

- Làm rõ boundary trung tính của window pipeline quanh `VLMQueue`/`VLMWorker`
  mà không bắt buộc đổi tên ngay; giữ alias tương thích vì queue hiện tại chạy
  toàn bộ video window pipeline chứ không chỉ VLM.
- `AnalysisStore.complete_processed_window()` đã nhận
  `ProcessedVideoWindow`; tiếp tục dùng contract này thay vì quay lại `dict`.
- Bổ sung integration test dùng video source thật cho: segment 0 enqueue ngay,
  segment 1 không phát trước 5 giây, và kết quả VLM window đi qua
  `/analyses/{id}`.
- Thêm metric wall-clock per-window (`queued_at`, `started_at`, `completed_at`)
  tách khỏi tổng stage time; tổng stage time không biểu diễn thứ tự thực thi.
- Chuẩn hóa metadata boundary cho stage: `analysis_id`, `camera_id`,
  `window_index`, source timestamps, stage status và lỗi có thể serialize được.

---

## Thứ tự thực thi đã chốt

```text
1. Hoàn thiện benchmark runner và ghi baseline Qwen 4B + 2 frame
2. Thêm Event Router bảo thủ
3. Nối Observation → Candidate → Decision → Alert và prompt theo candidate vào runtime
4. Benchmark 2B so với 4B; benchmark 2/4/6 keyframe
5. Chỉ quyết định cascade sau khi có báo cáo benchmark
6. Fire/Smoke chuyên biệt kèm temporal validation
7. ByteTrack tối thiểu trên nhánh opt-in
8. Zone/Line cơ bản
9. Rule Engine giao thông
10. Tối ưu keyframe, model routing và tài nguyên dựa trên số đo
```

Task 1 và Task 2 bên dưới là foundation đã hoàn thành. Task tiếp theo phải là
Task 5 (benchmark runner/baseline), sau đó mới thực hiện Task 3 và Task 4.
Không dùng thứ tự xuất hiện lịch sử của các task để bỏ qua benchmark.

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
- Keep `src/camera_ai/queue.py` as the current execution adapter; do not split it into per-stage queues in this roadmap.
- Create `src/camera_ai/tracking.py` and `tests/test_tracking.py` only after the event foundation is merged.
- Create `src/camera_ai/spatial_rules.py` and `tests/test_spatial_rules.py` after tracking is validated.
- Create `src/camera_ai/traffic_rules.py` and `tests/test_traffic_rules.py` after Zone/Line.

### Task 1: Capture the baseline benchmark contract

**Status:** Completed in commit `050fe2f` (`test: define camera ai benchmark baseline`).

**Files:**
- Create: `docs/benchmarks/README.md`
- Create: `src/camera_ai/benchmark.py`
- Create: `tests/test_benchmark_metrics.py`

**Interfaces:**
- Consumes: existing `PipelineResult`, `VideoAnalysisStats`, `VideoWindowResult`.
- Produces: `BenchmarkCase`, `BenchmarkObservation`, `BenchmarkSummary`, and a JSON-serializable metrics report.

- [x] **Step 1: Write failing metric tests**

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

- [x] **Step 2: Run `pytest tests/test_benchmark_metrics.py -q` and verify it fails because the benchmark types/functions do not exist.**
- [x] **Step 3: Implement Pydantic models and deterministic aggregation in `src/camera_ai/benchmark.py`.**
- [x] **Step 4: Add `docs/benchmarks/README.md` with normal, accident, fall, fighting, fire/smoke, hard-negative, and tamper categories; document JSON annotations without requiring video files in the repository.**
- [x] **Step 5: Run `pytest tests/test_benchmark_metrics.py -q`; expected result: all tests pass.**
- [x] **Step 6: Commit with `git add src/camera_ai/benchmark.py tests/test_benchmark_metrics.py docs/benchmarks/README.md && git commit -m "test: define camera ai benchmark baseline"`.**

**Extensibility checkpoint:** The benchmark report must expose stage-level
timing fields even when the current worker executes stages sequentially. This
keeps the report useful if a later implementation moves a stage to another
worker without changing the benchmark case format.

### Task 2: Add explicit observation, candidate, decision, and alert contracts

**Status:** Completed in commit `a15c820` (`feat: add camera event data contracts`).

**Files:**
- Modify: `src/camera_ai/schemas.py`
- Create: `src/camera_ai/event_models.py`
- Create: `tests/test_event_contracts.py`

**Interfaces:**
- Consumes: `EventObject`, `MotionResult`, `Detection`, `VideoFrameObservation`, `VideoWindowResult`.
- Produces: `VideoWindowObservation`, `CandidateEvent`, `ModelDecision`, `AlertEvent`, `DecisionValue`, `Priority`, and `project_alert_to_pipeline_result(...)` from `src/camera_ai/event_models.py`.

- [x] **Step 1: Write failing schema tests**

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

- [x] **Step 2: Run `pytest tests/test_event_contracts.py -q` and verify it fails.**
- [x] **Step 3: Add strict enums for decision, priority, severity, and alert status; include `raw_output_valid`, optional confidence for analysis, evidence, and latency in `src/camera_ai/event_models.py`.**
- [x] **Step 4: Implement stable IDs from request/window context and project the highest-priority verified result into existing `SecurityDecision`/`VLMResult` fields.**
- [x] **Step 5: Add an explicit adapter from domain `AlertEvent` to the existing `alert_store.Alert`; preserve `VLMResult.status` as `pending`, `completed`, or `skipped`.**
- [x] **Step 6: Run the new tests plus `pytest tests/test_video_pipeline.py -q`; expected result: all pass and existing schemas remain valid.**
- [x] **Step 7: Commit with `git add src/camera_ai/schemas.py src/camera_ai/event_models.py tests/test_event_contracts.py && git commit -m "feat: add camera event data contracts"`.**

**Boundary rule:** Domain contracts live in `schemas.py` and
`event_models.py`; transport contracts remain in `events.py` and `queue.py`.
Do not make a domain model depend on `asyncio`, FastAPI, Ollama, or RabbitMQ.

### Task 3: Build the deterministic Event Router

**Status:** Implemented locally; focused router/contract tests pass. Commit pending.

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

### Task 4: Integrate Observation → Candidate → Decision → Alert without changing sampling or call counts

**Status:** Core typed lifecycle, compatibility metadata, queue timing, and
opt-in artifacts are implemented locally. The current adapter derives a
conservative decision from the existing `SceneAnalysis`; candidate-specific
raw prompt tracing remains follow-up work before calling this task complete.

**Files:**
- Modify: `src/camera_ai/pipeline.py`
- Modify: `src/camera_ai/schemas.py`
- Modify: `src/camera_ai/event_models.py`
- Modify: `src/camera_ai/video_windows.py`
- Modify: `src/camera_ai/queue.py`
- Create: `src/camera_ai/artifacts.py`
- Modify: `tests/test_video_pipeline.py`
- Create: `tests/test_pipeline_events.py`
- Create: `tests/test_artifacts.py`

**Interfaces:**
- Consumes: current `RawVideoWindow`, `ProcessedVideoWindow`, `VideoFrameObservation`, `select_keyframes`, `route_observation`, and the existing async `VLMTask`/`AlertStore` lifecycle.
- Produces: one `VideoWindowObservation`, zero or more `CandidateEvent` records, at most one primary verification candidate per existing Qwen call, zero or more `ModelDecision`/`AlertEvent` records, and optional reproducible artifacts. Existing `PipelineResult` remains populated and async results retain `pending → completed/skipped` status.

- [ ] **Step 1: Write failing integration tests asserting that a synthetic async motion window exposes observation, candidate, decision, and alert metadata while the existing Qwen call count remains unchanged. Assert that an async static window retains its current one-call-per-window behavior during this compatibility task.**
- [ ] **Step 2: Run `pytest tests/test_pipeline_events.py tests/test_video_pipeline.py -q` and verify the new assertions fail.**
- [ ] **Step 3: Build `VideoWindowObservation` from the observations and selected keyframes already owned by `VideoWindowProcessor`; do not change `motion_fps=5`, `yolo_fps=2`, `max_keyframes=2`, window duration, or frame selection.**
- [ ] **Step 4: Call `route_observation()` once per closed window and choose at most one primary verification candidate deterministically by `Priority` then stable `candidate_id`. Keep additional candidates as sidecar metadata so one window still makes at most one existing Qwen call.**
- [ ] **Step 5: Extend the VLM request/parse boundary to record the selected candidate, exact prompt, raw output validity, and `yes|no|uncertain` decision without trusting self-reported confidence. Convert only a valid affirmative decision through explicit policy into `AlertEvent`; invalid schema becomes `uncertain`, not an alert.**
- [ ] **Step 6: Keep async video on the existing `raw_window → VLMWorker → pipeline.process_video_window()` path. Persist the new sidecar fields through `AnalysisStore.complete_processed_window()`; do not create a second worker and do not route video through the image-oriented `analyze_vlm()` branch.**
- [ ] **Step 7: Add opt-in artifact writing controlled by an explicit output directory. For each `camera_id/window_id`, atomically write `observation.json`, `candidate.json`, `selected_frame_*.jpg`, `vlm_prompt.txt`, `vlm_raw_output.txt`, `decision.json`, and `alert.json` only when enabled. Redact image bytes/base64 from JSON.**
- [ ] **Step 8: Add integration assertions for `AlertStore.Alert.vlm.status`, `VLMQueue` enqueue/update behavior, invalid JSON → uncertain/no-alert behavior, and artifact filenames. Preserve existing API fields additively.**
- [ ] **Step 9: Run `pytest -q`; expected result: existing behavior and new event assertions pass.**
- [ ] **Step 10: Commit with `git add src/camera_ai/pipeline.py src/camera_ai/schemas.py src/camera_ai/event_models.py src/camera_ai/video_windows.py src/camera_ai/queue.py src/camera_ai/artifacts.py tests/test_video_pipeline.py tests/test_pipeline_events.py tests/test_artifacts.py && git commit -m "feat: persist window event lifecycle"`.**

**Execution constraint:** Keep one current `VLMWorker` path for async video.
The integration is complete when the new stage artifacts are available through
the existing lifecycle, not when multiple queues exist.

### Task 5: Add the offline benchmark runner and baseline report

**Execution gate:** This is the next implementation task. Complete and record
the Qwen 4B + two-frame baseline before Task 3 or Task 4 changes runtime output.

**Status:** Async HTTP runner and metrics are implemented. A one-video smoke
baseline was recorded for 4B and 2B; a balanced normal/abnormal manifest is
still required before the baseline gate is considered complete.

**Files:**
- Create: `scripts/benchmark_pipeline.py`
- Modify: `src/camera_ai/schemas.py`
- Modify: `src/camera_ai/benchmark.py`
- Modify: `src/camera_ai/video_windows.py`
- Modify: `docs/benchmarks/README.md`
- Create: `tests/test_benchmark_cli.py`
- Modify: `tests/test_benchmark_metrics.py`

**Interfaces:**
- Consumes: a manifest JSON, local media paths, `SecurityAIPipeline`, and optional `--model`/`--frame-mode` settings; exercise the default async producer, queue, worker, and analysis completion lifecycle.
- Produces: one JSONL per case and one aggregate JSON report containing async stage latency, queue wait, time to first result, wall-clock completion time, VLM calls, JSON validity, predicted/expected event, and false-positive/false-negative counts.

- [ ] **Step 1: Write failing metric tests for `keyframe_ms`, queue wait/wall-clock latency, `json_valid`, false positives, and false negatives. Write a CLI test using a temporary manifest and mocked pipeline; assert one result line per case and one summary object.**
- [ ] **Step 2: Run `pytest tests/test_benchmark_cli.py -q` and verify it fails because the script does not exist.**
- [ ] **Step 3: Add `keyframe_ms` to stage timing and measure `select_keyframes()` directly. Extend benchmark observations with queue/wall-clock fields and JSON validity while preserving Pydantic defaults for old API payloads.**
- [ ] **Step 4: Implement arguments `--manifest`, `--output`, `--model`, `--frame-mode`, and `--warmup`; use the default async path and the same media for every compared configuration. Do not add a sync/async mode switch to the first runner.**
- [ ] **Step 5: Document warm-up, cold-run exclusion, Ollama availability, manifest labels, reviewer ground truth, and required case fields.**
- [ ] **Step 6: Run `pytest tests/test_benchmark_cli.py tests/test_benchmark_metrics.py tests/test_video_pipeline.py -q`; expected result: CLI/metrics/timing tests pass. Run a dry fixture and verify JSONL plus summary JSON are created.**
- [ ] **Step 7: Run the fixed manifest through async with Qwen 4B, two composite keyframes, and production defaults; save the dated async baseline report before continuing to Event Router integration.**
- [ ] **Step 8: Commit with `git add scripts/benchmark_pipeline.py src/camera_ai/schemas.py src/camera_ai/benchmark.py src/camera_ai/video_windows.py tests/test_benchmark_cli.py tests/test_benchmark_metrics.py docs/benchmarks/README.md && git commit -m "feat: add reproducible pipeline benchmark runner"`.**

### Task 6: Benchmark keyframe modes and Fast 2B versus Strong 4B

**Status:** Configuration validation and one-case 2B/4B composite smoke runs
are complete. Separate 2/4/6-frame quality runs, RAM/VRAM capture, and a
production routing decision remain pending. Cascade stays disabled.

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

**Cascade decision gate:** This task produces evidence and a decision, not a
production cascade. Create a separate implementation plan only if 2B handles
the measured majority of candidates, improves end-to-end latency materially,
keeps quality within the accepted benchmark threshold, and model-switch cost
is acceptable. Initial escalation may use only `uncertain`, invalid schema,
critical candidates, or conflict with deterministic/specialized evidence;
self-reported confidence remains analysis-only.

### Task 7: Integrate specialized Fire/Smoke detection with temporal validation

**Status:** Implemented locally as an opt-in detector path with three-frame
temporal confirmation and explicit `model`/`heuristic` backend evidence.
Default pipeline construction still excludes FireDetector. Commit pending.

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
- Verify an async static video does not call YOLO and currently calls Qwen once
  per window until a measured change introduces an async motion gate.
- Verify async motion with no YOLO detection still reaches Qwen.
- Verify Ollama failure returns `degraded=true` and does not create a high alert.
- Verify async requests expose `pending` first and reach `completed` or `skipped` through the existing `VLMWorker` and `AlertStore`.
- Verify `events.py` remains the EventBus module and domain contracts live in `event_models.py`.
- Verify candidate names remain hypotheses and confirmed alert names only appear in policy output.
- Verify ByteTrack, Zone/Line, and traffic rules are opt-in and covered by isolated tests.
- Record benchmark results before selecting a 2B/4B cascade or changing keyframe count.
- Verify the artifact bundle can explain whether an error originated in
  Motion, YOLO, keyframe selection, routing, VLM decision, or alert policy.
