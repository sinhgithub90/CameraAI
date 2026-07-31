# Compact Qwen Request Design

## Goal

Reduce warm Qwen-VL latency for an active five-second video window without
changing the `qwen3-vl:4b-instruct-q4_K_M` model or the two separate temporal
keyframes sent to it.

## Request shape

Keep the existing Ollama `/api/chat` integration, context size, temperature,
keep-alive behavior and two JPEG images. Replace the long prompt description
of the output contract with a short Vietnamese security-analysis instruction.
Pass an Ollama JSON schema through the top-level `format` field so the model
must return exactly these fields:

- `alert_level`: `low`, `medium` or `high`;
- `summary`: one short Vietnamese sentence;
- `risks`: a short list of Vietnamese security risks;
- `recommended_action`: one short Vietnamese action.

Set the default `num_predict` to 96. Preserve `OLLAMA_NUM_PREDICT` as an
environment override.

## Detector context

Compact repeated YOLO detections by label. Send one line per label containing
the count and highest confidence, for example:

```text
person: count=2, max_conf=0.91
car: count=1, max_conf=0.87
```

Do not send bounding boxes. The complete detections remain unchanged in the
pipeline result and annotated output; only Qwen's textual context is compacted.

## Compatibility

The public `SceneAnalysis` and API response stay backward compatible.
Because Qwen no longer generates `observations`, the adapter derives
`observations` as `[summary]` when the summary is non-empty and otherwise uses
an empty list. Existing parsing remains tolerant of extra fields and malformed
responses continue to use the current degraded-text behavior.

## Error handling

Connection failures, timeouts and non-JSON model responses retain the existing
degraded result behavior. A 96-token limit may truncate an unexpectedly verbose
response; the strict schema and short-field instructions are intended to keep
normal responses below that limit.

## Testing and verification

- Assert that Ollama receives the compact JSON schema in `format`.
- Assert that the default output limit is 96 and the environment override still
  works.
- Assert that repeated detections become label counts and maximum confidence,
  with no bounding-box text.
- Assert that a valid compact response is mapped to the unchanged public model,
  including derived `observations`.
- Run the complete test suite.
- Compare one warm two-frame request before and after the change using Ollama's
  prompt/output timing logs. Report the measurement without a flaky timing
  assertion in automated tests.

