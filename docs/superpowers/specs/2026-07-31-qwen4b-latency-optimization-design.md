# Qwen 4B Latency Optimization Design

## Goal

Reduce the latency of one Qwen analysis for a five-second video window while
retaining the current Qwen 4B model and maximum of four chronological
keyframes. The output must remain valid structured Vietnamese security
analysis.

## Evidence and chosen approach

The current `num_ctx=8192` configuration loads the model as 83% GPU and 17%
CPU on the RTX 3060 Laptop GPU. A diagnostic request with `num_ctx=4096`
loads it as 100% GPU. Four frames use about 2,500 prompt tokens. Reducing test
images from 1280x720 to 640x360 did not reduce visual token count, so image
resolution is not part of this change.

The implementation will use the balanced approach: retain Qwen 4B and four
keyframes, but optimize request configuration and bound text input/output.
Switching to Qwen 2B would be faster but changes analysis quality; merely
resizing frames did not address the measured bottleneck.

## Request configuration

- Change the default Ollama context from 8192 to 4096. Keep the existing
  `OLLAMA_NUM_CTX` override.
- Send `keep_alive="10m"` so repeated tests do not pay model cold-load after
  short idle periods. Allow `OLLAMA_KEEP_ALIVE` to override it.
- Set `num_predict=160` to bound response generation while leaving enough room
  for the required JSON. Allow `OLLAMA_NUM_PREDICT` to override it.
- Set `temperature=0` to make short strict-JSON output more deterministic.
- Continue using non-streaming `/api/chat` because the pipeline needs the full
  JSON before constructing `SceneAnalysis`.

## Detection prompt compaction

Detector output across sampled video frames can contain repeated instances of
the same object. Before formatting the prompt, group detections by label and
include at most three highest-confidence detections per label, with a maximum
of twelve detection lines total. Preserve labels, confidence values and
bounding boxes so Qwen still has useful spatial context.

This compaction applies inside the Qwen adapter only. The pipeline result and
UI continue to receive the complete detector output.

## Telemetry

Read Ollama timing fields from the successful response and log total, load,
prompt-evaluation and generation durations, along with prompt and generated
token counts. Do not change the public response schema in this optimization;
the existing `qwen_ms` remains the end-to-end latency visible in terminal,
JSON and UI.

## Error handling

Existing degraded behavior for connection errors and invalid model output is
unchanged. A response truncated before valid JSON is treated by the existing
parser as degraded text; `num_predict=160` is deliberately above the observed
50-122 token range to avoid normal truncation.

## Verification

- Unit tests assert the optimized request options and keep-alive value.
- Unit tests assert deterministic detection compaction and its limits.
- Existing parser, video pipeline and model-override tests continue to pass.
- A local benchmark compares cold and warm four-frame calls and records
  `ollama ps` processor placement. Success means the model is 100% GPU at the
  default context and a warm call returns valid JSON; elapsed time is reported
  rather than enforced in a flaky timing assertion.
