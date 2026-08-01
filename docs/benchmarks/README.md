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

## Metrics contract

Each `BenchmarkObservation` records exactly one expected event, one predicted
event, latency, VLM call count, and `StageTiming` values (`total_ms`,
`motion_ms`, `detector_ms`, and `qwen_ms`). `summarize_benchmark` returns a
JSON-serializable `BenchmarkSummary`, including mean stage timings. Recall for
an event is correct predictions for that event divided by all observations
whose expected event is that event. Empty inputs produce zero counts/timings
and an empty recall map deterministically.
