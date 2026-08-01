# Window Pipeline Queue Implementation Plan

**Goal:** Treat an uploaded video as a real-time stream: buffer exactly one five-second window, enqueue the raw window, then process Motion, YOLO, keyframe selection, and VLM in one worker.

**Architecture:** The producer is limited to paced frame acquisition and window boundaries. `WindowPipelineQueue` owns raw `VideoWindowTask` items and one `WindowPipelineWorker` runs all inference stages for one item before dequeuing the next. `analysis_id` and `window_index` stay on every item so later stage-specific queues can reuse the same contracts.

## Constraints

- A file replay is paced to its source timestamps; window 1 is not emitted before five seconds of source time.
- There is one process-wide queue and one worker.
- The worker processes Motion → YOLO → keyframe selection → VLM in that order for a window.
- The UI reads only aggregate analysis state; it never renders detections for video.

## Tasks

1. Add a raw `VideoWindowTask` and single queue worker test that proves Motion/YOLO/VLM are called in order for one queued window.
2. Refactor the video producer to collect only raw sampled frames and pace replay before emitting each completed five-second window.
3. Move per-window Motion/YOLO/keyframe/VLM work into the queue worker and replace the pending aggregate window with its completed result.
4. Update the API wiring and tests; verify a later video window cannot be queued before its five-second source interval has elapsed.
