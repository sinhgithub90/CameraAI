# Streaming Video Window Producer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start VLM analysis for each completed five-second video window immediately, using the existing single global `VLMQueue`, while the next window is still being read and detected.

**Architecture:** `/async/analyze/video` creates one `analysis_id` and starts a background producer. The producer reads one 5-second window at a time, runs Motion and YOLO, selects up to two keyframes, records its timing, appends the window to an in-memory analysis record, then enqueues one `VLMTask` into the existing global queue. `VLMWorker` remains the sole queue consumer and updates the owning window through the analysis record.

**Tech Stack:** Python 3.11+, asyncio, OpenCV, Pydantic, FastAPI, existing `VLMQueue`/`VLMWorker`, pytest.

## Global Constraints

- There is exactly one process-wide `VLMQueue` and one `VLMWorker` unless multi-worker support is explicitly added later.
- Every completed 5-second window produces one VLM task, including calm windows.
- Each VLM task contains at most two selected keyframes.
- The producer must enqueue window 0 before decoding window 1; it must not build the full window list first.
- VLM tasks remain priority ordered in the shared queue and may interleave with tasks from another upload.
- FE must not render detections or bounding boxes for video results.
- FE shows aggregate Motion/YOLO/VLM stage timings and one VLM result card per window.
- `PipelineResult` sync endpoints remain backward compatible.

---

## File map

- Create `src/camera_ai/analysis_store.py`: owns async video analysis lifecycle and window aggregation.
- Modify `src/camera_ai/pipeline.py`: expose callback-based streaming window production; retain `detect_video_windows()` for compatibility tests.
- Modify `src/camera_ai/queue.py`: carry `analysis_id` and `window_index` in `VLMTask`; update the matching aggregate window after VLM completes.
- Modify `apps/api/main.py`: create an analysis record, return immediately, and schedule the producer as a background task.
- Modify `apps/api/static/index.html`: poll one analysis endpoint and render timing/window cards.
- Create `tests/test_analysis_store.py` and `tests/test_streaming_video_producer.py`.
- Modify `tests/test_queue.py`, `tests/test_async_video_response.py`, and `tests/test_async_video_ui.py`.

### Task 1: Add an aggregate analysis store

**Files:**
- Create: `src/camera_ai/analysis_store.py`
- Create: `tests/test_analysis_store.py`

**Interfaces:**
- Consumes: `VideoWindowResult`, `SceneAnalysis`, `StageTiming`.
- Produces: `VideoAnalysis`, `AnalysisStore`, `InMemoryAnalysisStore`.
- Required methods: `create(analysis)`, `append_window(analysis_id, window)`, `complete_window(analysis_id, alert_id, analysis, qwen_ms)`, `mark_producer_complete(analysis_id)`, `mark_producer_failed(analysis_id, detail)`, and `get(analysis_id)`.

- [ ] **Step 1: Write failing lifecycle tests**

```python
@pytest.mark.asyncio
async def test_analysis_store_aggregates_completed_window_timing():
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="a1", camera_id="cam_01"))
    await store.append_window("a1", pending_window(alert_id="alert-0"))
    await store.complete_window("a1", "alert-0", scene_analysis, qwen_ms=50.0)
    analysis = await store.get("a1")
    assert analysis.windows[0].vlm.status == "completed"
    assert analysis.total_timing.qwen_ms == 50.0
```

- [ ] **Step 2: Run `python -m pytest tests/test_analysis_store.py -q` with `PYTHONPATH=src`; expected result: fail because the store does not exist.**
- [ ] **Step 3: Implement Pydantic lifecycle models and the in-memory store. Aggregate timing as the sum of all window `StageTiming` values.**
- [ ] **Step 4: Add failure-state tests so a producer error remains visible through `GET /analyses/{analysis_id}` instead of leaving an analysis permanently pending.**
- [ ] **Step 5: Run `python -m pytest tests/test_analysis_store.py -q`; expected result: all tests pass.**
- [ ] **Step 6: Commit with `git add src/camera_ai/analysis_store.py tests/test_analysis_store.py && git commit -m "feat: add async video analysis store"`.**

### Task 2: Stream one detected window at a time

**Files:**
- Modify: `src/camera_ai/pipeline.py`
- Create: `tests/test_streaming_video_producer.py`
- Modify: `tests/test_video_pipeline.py`

**Interfaces:**
- Consumes: `EventObject`, `MotionDetector`, `Detector`, `select_keyframes`.
- Produces: `stream_video_windows(event, on_window, max_windows=None) -> None`, where `on_window(window: dict)` is invoked once per completed 5-second window in chronological order.

- [ ] **Step 1: Write a failing callback-order test**

```python
def test_stream_video_windows_emits_first_window_before_second_is_read(tmp_path):
    observed = []
    pipeline.stream_video_windows(event, observed.append)
    assert [window["window_index"] for window in observed] == [0, 1]
    assert all(len(window["frames"]) <= 2 for window in observed)
```

- [ ] **Step 2: Run `python -m pytest tests/test_streaming_video_producer.py -q` with `PYTHONPATH=src`; expected result: fail because `stream_video_windows` does not exist.**
- [ ] **Step 3: Extract the current window-reading loop into `stream_video_windows`. Flush window 0 through `on_window` immediately when the source timestamp enters window 1; do not accumulate a full video list. Preserve Motion and YOLO timing in each emitted dict.**
- [ ] **Step 4: Implement `detect_video_windows` as a compatibility wrapper that appends emitted windows to a list.**
- [ ] **Step 5: Add a calm-video test confirming each 5-second window still emits two keyframes and one future VLM task candidate.**
- [ ] **Step 6: Run `python -m pytest tests/test_streaming_video_producer.py tests/test_video_pipeline.py -q`; expected result: all tests pass.**
- [ ] **Step 7: Commit with `git add src/camera_ai/pipeline.py tests/test_streaming_video_producer.py tests/test_video_pipeline.py && git commit -m "feat: stream video windows as they complete"`.**

### Task 3: Enqueue streamed windows into the one global queue

**Files:**
- Modify: `src/camera_ai/queue.py`
- Modify: `src/camera_ai/alert_store.py`
- Modify: `apps/api/main.py`
- Modify: `tests/test_queue.py`
- Modify: `tests/test_async_video_response.py`

**Interfaces:**
- Consumes: streamed window dict, `InMemoryAnalysisStore`, global `VLMQueue`, global `VLMWorker`.
- Produces: one `VLMTask` per window with `analysis_id`, `window_index`, `alert_id`, selected frames, detections, and timing.

- [ ] **Step 1: Write a failing test that runs a two-window producer with a recording queue and asserts the first enqueue occurs before the producer emits the second window.**
- [ ] **Step 2: Run `python -m pytest tests/test_async_video_response.py tests/test_queue.py -q` with `PYTHONPATH=src`; expected result: fail because tasks lack aggregate-window identity.**
- [ ] **Step 3: Add `analysis_id` and `window_index` to `VLMTask`. Pass the shared `AnalysisStore` to `VLMWorker`; after VLM finishes, update both the existing AlertStore record and the matching aggregate window.**
- [ ] **Step 4: In `/async/analyze/video`, create `VideoAnalysis`, return its ID immediately, and schedule one background producer task. In the producer thread, submit each emitted window to the event loop with `asyncio.run_coroutine_threadsafe`; create its Alert and enqueue the one global `VLMQueue`.**
- [ ] **Step 5: Ensure producer exceptions call `mark_producer_failed`, and use `finally` to remove any temporary upload file.**
- [ ] **Step 6: Run `python -m pytest tests/test_async_video_response.py tests/test_queue.py tests/test_streaming_video_producer.py -q`; expected result: all tests pass.**
- [ ] **Step 7: Commit with `git add apps/api/main.py src/camera_ai/queue.py src/camera_ai/alert_store.py tests/test_queue.py tests/test_async_video_response.py && git commit -m "feat: enqueue video windows while reading"`.**

### Task 4: Expose one analysis endpoint and render per-window VLM output

**Files:**
- Modify: `apps/api/main.py`
- Modify: `apps/api/static/index.html`
- Modify: `tests/test_async_video_ui.py`
- Create: `tests/test_analysis_endpoint.py`

**Interfaces:**
- Consumes: `analysis_id` returned by `POST /async/analyze/video`.
- Produces: `GET /analyses/{analysis_id}` returning producer state, aggregate timing, and chronological `VideoWindowResult` list.

- [ ] **Step 1: Write a failing endpoint test**

```python
def test_get_analysis_returns_windows_and_total_timing(client, populated_store):
    response = client.get("/analyses/a1")
    assert response.status_code == 200
    body = response.json()
    assert body["windows"][0]["timing"]["qwen_ms"] == 50.0
    assert body["total_timing"]["qwen_ms"] == 50.0
```

- [ ] **Step 2: Run `python -m pytest tests/test_analysis_endpoint.py -q` with `PYTHONPATH=src`; expected result: fail because the endpoint does not exist.**
- [ ] **Step 3: Add `GET /analyses/{analysis_id}`. Return `404` only for an unknown ID; return a valid `reading`, `queued`, `completed`, or `failed` state for every created analysis.**
- [ ] **Step 4: Change FE polling from `GET /alerts/{id}` to `GET /analyses/{analysis_id}`. Render aggregate Motion/YOLO/VLM timing and a card for each chronological window. Keep cards pending until their VLM result arrives; do not render detection labels, counts, or bounding boxes.**
- [ ] **Step 5: Add a UI regression test asserting that polling uses `/analyses/`, rendering includes timing/window cards, and the removed detection table is absent.**
- [ ] **Step 6: Run `python -m pytest tests/test_analysis_endpoint.py tests/test_async_video_ui.py -q`; expected result: all tests pass.**
- [ ] **Step 7: Commit with `git add apps/api/main.py apps/api/static/index.html tests/test_analysis_endpoint.py tests/test_async_video_ui.py && git commit -m "feat: poll streaming video analysis results"`.**

## Verification checklist

- Run `PYTHONPATH=src python -m pytest -q` from `D:\CongViec\CameraAI\CameraAI`.
- Upload a video longer than 10 seconds in async mode.
- Confirm window 0 is enqueued while window 1 is being decoded by checking queue/worker logs.
- Confirm there is one `VLMQueue` and one `VLMWorker`, not one queue per window.
- Confirm every 5-second window appears in `GET /analyses/{analysis_id}`, including calm windows.
- Confirm every window has no more than two VLM frames.
- Confirm FE shows Motion/YOLO/VLM aggregate timings plus each VLM result, without object detection rows.
- Confirm a VLM failure marks only its window degraded and producer failure marks the analysis failed.
