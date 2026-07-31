# Qwen Composite Keyframes Design

## Goal

Reduce warm Qwen-VL latency while preserving the visual information from the
two event-aware keyframes selected for each active five-second video window.
YOLO, Motion, keyframe selection, the Qwen 4B model and the public API response
remain unchanged.

## Selected approach

Composite mode becomes the default. `OllamaQwenAnalyzer` will expose
`OLLAMA_FRAME_MODE` with two accepted values:

- `composite` (default): when exactly two frames are supplied, combine them
  into one labelled image before JPEG encoding;
- `separate`: retain the current behavior and send the two JPEG images
  independently.

Invalid values fail fast with `ValueError`. A single image remains a single
unchanged image in either mode. Frame sequences with a count other than two
remain separate so the adapter does not silently discard or reinterpret input
from callers outside the current video pipeline.

## Composite layout

Each source frame is fitted into a 960 x 540 panel while preserving aspect
ratio. Empty space is black. Use linear interpolation when downscaling. Overlay
an ASCII label that OpenCV can render reliably: `TRUOC` on the first panel and
`SAU` on the second. Stack the panels vertically into one 960 x 1080 BGR image.

The prompt states that the upper panel is before and the lower panel is after.
Only the Ollama adapter sees the composite; the pipeline still retains the
original frames for YOLO detections, representative selection and annotation.

## Request and output behavior

Composite mode sends one base64 JPEG in `messages[0].images`; separate mode
sends two. JSON schema, `num_ctx=4096`, `num_predict=96`, keep-alive, compact
YOLO context and the four-field Qwen output remain unchanged.

Log the selected frame mode, source frame count, sent image count and composite
dimensions. Existing Ollama timing logs remain the source for prompt token and
latency comparison.

## Error handling and rollback

JPEG encoding and Ollama request failures keep their existing behavior. A
failure while constructing a two-frame composite raises an explicit runtime
error instead of silently switching modes. Operators can immediately restore
the old request shape without a code rollback:

```powershell
$env:OLLAMA_FRAME_MODE = "separate"
```

Restart or reload the API after changing the environment variable.

## Verification

- Unit-test composite dimensions, labels/order and aspect-ratio preservation.
- Unit-test that default two-frame analysis sends one image.
- Unit-test that `separate` sends two images and invalid modes fail fast.
- Unit-test that one frame and non-two-frame sequences preserve their existing
  request shape.
- Run the complete automated test suite.
- Run the same warm video once in `separate` mode and once in `composite` mode.
  Compare Qwen prompt tokens, `prompt_ms`, `qwen_ms`, alert level and summary.
  Timing is reported rather than enforced in automated tests.

The trial is considered useful if the warm `qwen_ms` falls materially (target
0.3 to 0.8 seconds) without changing the alert conclusion for the reference
video. If detail loss is unacceptable, use `separate` mode.

