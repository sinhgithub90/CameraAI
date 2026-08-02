# Compact Benchmark JSON Design

## Goal

Reduce each per-video benchmark JSON file to the fields needed to review VLM results and pipeline performance. Preserve one entry for every five-second window, including green windows.

## Alert level source

For a confirmed alert, `alert_level` is derived from `event_metadata.alert.severity`. This makes confirmed events such as `traffic_accident` appear as `high` even when the VLM scene itself has a low router priority.

When no confirmed alert exists, the report falls back to `security.alert_level`. Unknown values fall back to `low`.

The top-level `level_summary` is calculated from these resolved per-window levels.

## Per-window schema

Each window contains:

```json
{
  "window_index": 1,
  "start_seconds": 5.0,
  "end_seconds": 10.0,
  "alert_level": "high",
  "candidate_type": "vehicle_scene",
  "qwen": {
    "called": true,
    "decision": "yes",
    "event_type": "traffic_accident",
    "summary": "Xe buýt đỏ va chạm với xe ô tô màu trắng.",
    "timestamps_seconds": [5.8, 9.8]
  },
  "timing": {
    "motion_ms": 9.7,
    "detector_ms": 393.1,
    "keyframe_ms": 0.1,
    "qwen_ms": 4861.6,
    "total_ms": 5264.6,
    "queue_wait_ms": 5344.0,
    "wall_clock_ms": 10609.0,
    "within_budget": false
  }
}
```

`candidate_type` is the primary routed candidate type, or `null` when no candidate exists. For a skipped window, `qwen.called` is `false`; `decision` and `event_type` are `null`, while `summary` retains the pipeline's short explanation and timestamps remain available.

## Removed fields

The benchmark report no longer writes:

- `detection_summary`
- candidate IDs, window IDs, priority, evidence, and verification flags
- decision model, evidence, validity, confidence, and duplicated latency
- raw detection labels and frame indices
- separate `risks`, `recommended_action`, `raw_output_valid`, `candidates`, `decision`, and `vlm_call` objects

These values remain available inside the processing pipeline where needed; only the benchmark projection is reduced.

## Top-level schema

The report keeps `video`, `camera_id`, `analysis_id`, `status`, `level_summary`, `performance_summary`, and `windows`. `performance_summary` continues to report Qwen call counts/rate, processing p95, over-budget count, and the configured budget.

## Compatibility and validation

This is an intentional benchmark-output schema change. Tests will verify:

- confirmed `traffic_accident` severity produces a red/high window and summary count;
- absent alerts fall back to the scene security level;
- detection summaries and verbose metadata are absent;
- all requested timing fields remain present;
- called and skipped Qwen windows use the compact structure correctly.
