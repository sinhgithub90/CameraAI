# Pre-Queue Red Admission Design

## Goal

Prevent known-red camera windows from entering the global processing queue,
remove stale queued work when red is confirmed, and represent the skipped
period as one cooldown summary instead of per-window results.

## Scope

The current uploaded-video producer is the first implementation. It uses video
source seconds. The admission API accepts an explicit timebase so a live stream
adapter can later pass monotonic seconds without changing queue or state logic.

No per-camera queue, external broker, database migration, or UI is added.

## Producer admission

Before creating a `VideoWindowResult`, compatibility alert, or `VLMTask`, the
producer atomically asks the camera state store to admit the raw window.

- Normal: enqueue the window unchanged.
- Active red/orange before its deadline: do not create a task or per-window
  record; increment the analysis cooldown summary.
- Recheck due: reserve exactly one window and enqueue it.
- Recheck reserved/in flight: drop later windows and increment the summary.

The producer still reads/grabs the source so a live stream remains current. The
uploaded-video producer may still form a raw five-second boundary, but dropped
frames never reach Motion, Detection, routing, keyframe selection, or a queue.

## Reservation

Add a producer reservation flag to camera runtime state. A reservation returns
an opaque state version on `WindowAdmission`. The queued task carries this
version. `VideoWindowProcessor` accepts the reserved admission and does not
reject its own task as in-flight. Completion or failure clears the reservation.

This guarantees that only the first eligible recheck window enters the queue.

## Queue pruning

When a worker produces a newly created or extended red episode, remove queued
tasks for the same `(analysis_id, camera_id)` whose source start is before the
new recheck deadline. Queue pruning rebuilds the priority heap under one async
lock so cancelled work no longer contributes to queue depth or dequeue cost.

The pruning call returns removed tasks. Their pending analysis windows and
compatibility alerts are deleted. The currently executing task is never in the
heap and is unaffected.

## Cooldown summary

`VideoAnalysis` stores one additive summary:

- `active_alert_id`;
- `alert_level`;
- `timebase` (`video` or `monotonic`);
- `red_started`;
- `recheck_at`;
- `suppressed_windows`;
- `suppressed_seconds`.

Dropped future windows and pruned pending windows both increment the counts.
They are absent from `windows`. The compact `/analyses/{id}` response and
benchmark report expose the summary.

## Clocks

All state comparisons use a numeric value supplied by the caller:

- uploaded video: source seconds;
- live camera: `time.monotonic()`.

`timebase` is serialized with the value. A future live adapter may add UTC
display timestamps without changing admission decisions.

## Failure behavior

- A low recheck resolves the episode and normal admission resumes.
- A medium recheck uses the existing 15-second watch interval.
- A repeated red extends the same episode by 60 seconds and prunes again.
- A failed recheck preserves red and uses the existing processing-time retry
  backoff; producer admission continues dropping windows until retry is due.
- Producer or persistence failure keeps the analysis failed as today.

## Tests

Tests prove that known-red windows never call `VLMQueue.enqueue`, only one due
recheck is reserved, red confirmation removes same-stream backlog without
touching another camera, pruned/dropped windows disappear from public windows,
and cooldown summary counts remain correct. Existing processor-side admission
remains as defense in depth.
