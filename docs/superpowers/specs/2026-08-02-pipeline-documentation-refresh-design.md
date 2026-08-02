# Pipeline Documentation Refresh Design

## Goal

Update the current-user documentation so it matches the implemented async
video pipeline after producer-side red cooldown admission. Remove statements
that still describe cooldown windows as queued or persisted per-window.

## Scope

Update only:

- `README.md`;
- `docs/camera-ai-pipeline.md`;
- `docs/benchmarks/README.md`.

Do not rewrite the future system-architecture documents, change application
behavior, add UI documentation, or alter benchmark data files.

## Documentation model

All three documents use the same canonical flow:

```text
Read stream -> close 5-second boundary -> pre-queue admission
  active cooldown -> drop before queue, Motion, Detection, routing, and Qwen
  due recheck     -> atomically reserve exactly one window
  normal          -> global priority queue -> Motion -> Detection -> Router -> Qwen
```

When red is verified, the worker registers a same-stream cutoff and prunes
queued backlog for that `(analysis_id, camera_id)`. The cutoff also rejects a
stale task that was admitted just before red but arrives after pruning. Other
cameras and analyses remain untouched.

## State semantics

- Red recheck interval: 60 seconds in video source time.
- Medium/orange watch interval: 15 seconds.
- A low recheck resolves the episode and resumes normal admission.
- Failed rechecks preserve the alert and use bounded retry backoff.
- Uploaded video uses source seconds; a live adapter should use monotonic time.

The processor-side cooldown check remains documented as defense in depth, not
as the primary path.

## Public JSON contract

The `windows` array contains only windows that actually entered processing.
Dropped and pruned windows do not receive synthetic per-window records. Their
aggregate state is reported once under top-level `cooldown`:

- `active_alert_id`;
- `alert_level`;
- `timebase`;
- `red_started`;
- `recheck_at`;
- `suppressed_windows`;
- `suppressed_seconds`.

Benchmark call-rate denominators include both processed and suppressed
windows. Processing p95 covers work that ran; `queue_wait_ms` and
`wall_clock_ms` remain separate.

## Example

Use the current `RoadAccidents010_x264.json` report as a compact example:

- 0-5 seconds: green;
- 5-10 seconds: verified red;
- red begins at source second 10 and recheck is due at second 70;
- two later windows / 10 seconds are suppressed before queue;
- two Qwen calls out of four logical windows, call rate 0.5;
- queue wait is 0 ms and processing p95 is approximately 3,794 ms.

The example values are labeled as one observed run, not a guaranteed latency.

## Editing approach

Perform targeted edits. Preserve accurate setup, API, detector, routing, and
benchmark instructions. Replace contradictions, add the missing state/queue
flow, and keep examples compact instead of rewriting all three documents.

## Verification

- Search the scoped files for stale claims such as “pipeline has no cooldown”
  and “cooldown windows are still recorded.”
- Check that cooldown intervals and JSON field names agree across all files.
- Run Markdown link/path checks available in the repository and `git diff
  --check`.
