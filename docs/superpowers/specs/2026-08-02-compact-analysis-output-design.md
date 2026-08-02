# Compact Analysis Output Design

## Goal

Remove the unused demo UI and reduce both the public analysis response and the
per-video benchmark report to the information needed to review security alerts,
cooldown behavior, and performance.

## Scope

- Remove the root HTML route, static demo page, and UI source tests.
- Keep the internal `VideoAnalysis` and `VideoWindowResult` models rich enough
  for processing and diagnostics inside the application.
- Introduce a separate compact public response for `GET /analyses/{id}`.
- Make the benchmark consume the compact response and avoid rebuilding data
  from internal `event_metadata`.
- Remove redundant benchmark assertions while preserving behavior coverage.

Image endpoints, alert episode state, per-camera admission, queue behavior, and
the Motion/Detection/Qwen cooldown pipeline are unchanged.

## Public analysis contract

The top-level response retains:

- `id`, `camera_id`, `status`, and `error`;
- aggregate `total_timing`;
- `windows` in source-time order.

Each compact window retains:

- `window_index`, `start_seconds`, and `end_seconds`;
- resolved `alert_level`;
- `qwen.status`, `qwen.summary`, `qwen.degraded`, `qwen.verified`, and
  `qwen.reason`;
- `cooldown.active_alert_id` and `cooldown.next_recheck_seconds`;
- complete stage timing, including queue and wall-clock timing.

Fields with no value in the compact nested objects are omitted from serialized
JSON. Suppressed windows remain present, with inherited red, `verified=false`,
reason `active_alert_cooldown`, and zero Motion/Detection/keyframe/Qwen timing.

The public response excludes detections, risks, recommended action, Qwen input
frames, candidates, decisions, raw alert payloads, traces, and
`event_metadata`.

## Internal-to-public mapping

Create typed Pydantic response models and one conversion function at the
analysis boundary. The mapper reads `event_metadata.alert_context` and
`event_metadata.vlm_call` while those values are still internal. The API uses
the compact response model with `response_model_exclude_none=True`.

This preserves internal diagnostic information without exposing it or binding
the benchmark to private storage structures.

## Benchmark contract

`build_video_report` accepts the compact API payload. Its per-window output
uses the same compact `qwen`, cooldown, and timing values, plus
`candidate_type` only when the input explicitly contains one. Since the new API
does not expose candidate data, normal runtime reports omit it.

The report retains `level_summary` and `performance_summary`, including Qwen
call rate, cooldown suppression rate, episode/recheck counters, processing p95,
and budget failures. Episode counters are derived from compact transition flags
included in the API cooldown object:

- `episode_created`;
- `episode_extended`;
- `episode_resolved`;
- `recheck`.

These flags are omitted when false to keep normal and suppressed windows small.

## UI removal

Delete `apps/api/static/index.html`, the `GET /` handler, HTML-specific imports
and constants, and `tests/test_async_video_ui.py`. The FastAPI service remains
API-only. No replacement UI is included.

## Test strategy

Retain tests that protect state transitions, skip-before-Motion behavior,
persistence, worker stream identity, episode updates, API camera admission, and
benchmark cooldown metrics.

Add one API serialization test proving excluded internal fields are absent and
suppressed cooldown fields remain. Consolidate benchmark shape assertions so
there is one compact-contract test and one cooldown-metrics test. Remove only
UI source-string tests and redundant exact-dictionary assertions.

## Compatibility

This intentionally breaks consumers that relied on detailed window fields from
`GET /analyses/{id}` or the removed root UI. The async submission endpoint and
analysis polling URL are unchanged. Runtime state and inference behavior are
unchanged.
