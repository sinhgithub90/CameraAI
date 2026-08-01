# CameraAI benchmark fixtures

This directory documents the offline benchmark corpus; it does not store video
files. Keep the media in a controlled local or object-storage location and
version only the JSON manifest/annotations needed to reproduce a run.

## Required case categories

Include representative cases for each category below. Use a stable `case_id`
and one `expected_event` string per case.

- `normal`: ordinary activity with no event.
- `accident`: vehicle collision or comparable hazardous accident.
- `fall`: a person falling or remaining down after a fall.
- `fighting`: physical confrontation between people.
- `fire_smoke`: visible fire or smoke.
- `hard_negative`: visually similar but non-event activity, such as running,
  carrying objects, reflections, or permitted smoke-like scenes.
- `tamper`: obstruction, defocus, camera movement, or another camera-tamper
  condition.

## JSON annotations

Record annotations as JSON independently of the media. A minimal manifest item
uses the same labels as `BenchmarkCase`:

```json
{
  "case_id": "fall_001",
  "expected_event": "fall",
  "annotations": {
    "source": "local-fixture-store",
    "notes": "Fall begins near 00:04"
  }
}
```

The `annotations` object can contain JSON-safe contextual metadata, such as a
relative media reference, time range, camera conditions, or reviewer notes.
Do not commit the underlying video merely to make an annotation valid.

Hard-negative coverage for Fire/Smoke must explicitly include lamps,
sunlight, vehicle lights, billboards, orange clothing, welding, steam, and fog.

## Metrics contract

Each `BenchmarkObservation` records exactly one expected event, one predicted
event, latency, VLM call count, and `StageTiming` values (`total_ms`,
`motion_ms`, `detector_ms`, and `qwen_ms`). `summarize_benchmark` returns a
JSON-serializable `BenchmarkSummary`, including mean stage timings. Recall for
an event is correct predictions for that event divided by all observations
whose expected event is that event. Empty inputs produce zero counts/timings
and an empty recall map deterministically.

## Async smoke baseline (2026-08-01)

Manifest: `docs/benchmarks/manifests/road-accident-smoke.json`. This is one
7.5-second accident video and is only a runtime smoke comparison, not a quality
benchmark. It has no normal/hard-negative cases.

| Model | End-to-end | Qwen total | First result | JSON validity | Observed prediction |
|---|---:|---:|---:|---:|---|
| Qwen3-VL 4B Q4 | 24.22 s | 22.58 s | 18.62 s | 100% | `normal` |
| Qwen3-VL 2B Q4 | 18.83 s | 17.63 s | 13.97 s | 0% at case level | `vlm_truncated_response` |

The 2B smoke run was about 22% faster end-to-end, but one of two windows
returned truncated JSON. The 4B run returned valid JSON but did not identify
the annotated accident. Therefore this run does not justify a cascade, a
model switch, or a keyframe-count change. Keep production defaults at Qwen 4B
and two composite frames until a balanced manifest is available.

Generated reports live under `runs/benchmark/` and are intentionally not a
replacement for a versioned, balanced benchmark report.

## Alert-only video CLI

The CLI can process one video for a quick check or scan a directory
sequentially. It calls an already-running FastAPI async endpoint; it does not
start the server itself. Every attempted video creates one `<video-stem>.json`.
Completed reports contain every low/medium/high window and counts named
`green`, `orange`, and `red`. Failed videos also create JSON with `status` and
`error` so batch failures remain visible.

Each completed window also exposes routed `candidates`, the selected
`decision`, and `raw_output_valid`. The runtime default is 128 output tokens;
invalid or truncated candidate JSON becomes `uncertain` and cannot create an
alert.

```powershell
python -m scripts.benchmark_pipeline `
  --input-file "D:\CongViec\CameraAI\CameraAI\videos\RoadAccidents005_x264.mp4" `
  --output-dir "D:\CongViec\CameraAI\CameraAI\runs\alerts" `
  --base-url "http://127.0.0.1:8000"
```

```powershell
python -m scripts.benchmark_pipeline `
  --input-dir "D:\CongViec\CameraAI\CameraAI\videos" `
  --output-dir "D:\CongViec\CameraAI\CameraAI\runs\alerts" `
  --base-url "http://127.0.0.1:8000"
```

Use `--poll-interval` and `--timeout` to change polling behavior. Supported
directory extensions are `.mp4`, `.avi`, `.mov`, and `.mkv`.
### Qwen gate and five-second processing budget

Every per-video JSON includes `vlm_call` and `within_processing_budget` for each
window. `performance_summary` contains `vlm_called_windows`,
`vlm_skipped_windows`, `vlm_call_rate`, `processing_p95_ms`,
`windows_over_budget`, and the fixed `processing_budget_ms` value of 5000.

Run one arbitrary video quickly with:

```powershell
python -m scripts.benchmark_pipeline `
  --input-file "D:\CongViec\CameraAI\CameraAI\videos\RoadAccidents005_x264.mp4" `
  --output-dir "D:\CongViec\CameraAI\CameraAI\runs\alerts"
```

The API and Ollama must already be running. Do not treat the five-second target
as achieved until `processing_p95_ms <= 5000` and `windows_over_budget == 0` on
the target video set.

Raw detection bounding boxes stay inside the pipeline and API compatibility
model. Per-video benchmark JSON writes `detection_summary` instead, grouped by
label with `detection_count` and `max_confidence`. The count is the number of
raw YOLO observations, not a unique-object count; router evidence uses
frame-level peak counts and same-frame proximity statistics.
