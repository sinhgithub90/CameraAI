# Remove Fire Heuristic From Runtime Pipeline

## Goal

Make the runtime analysis path use only Motion, YOLO26n and Qwen-VL. Remove
all Fire heuristic calls so they cannot add false-positive `fire` detections
or consume detector time.

## Runtime flow

For video, sample motion at 5 FPS. When a five-second window contains motion,
run YOLO26n at no more than 2 FPS, select at most four chronological
keyframes, and send those frames plus compacted YOLO detections to Qwen. A
motion window still reaches Qwen when YOLO finds no objects, allowing Qwen to
recognize smoke, fire and other visual events directly.

For still images, run YOLO26n and use its detections as the existing VLM gate.
Under the default gated policy, an image with no YOLO detection skips Qwen.

## Compatibility

Remove the `fire_detector` dependency and calls from `SecurityAIPipeline`, and
simplify `VLMGate.decide` to consume only YOLO detections. Keep the standalone
`FireDetector` class and exports so existing imports do not fail, but the
default runtime pipeline never constructs or invokes it.

Pipeline output continues to expose the same detection schema. The `source`
field remains for backward compatibility, but normal runtime detections now
come only from YOLO26n.

## Testing and documentation

Update image and video tests to construct the pipeline without a fire
detector. Add regression assertions proving that normal pipeline construction
does not instantiate FireDetector and that video detector calls contain only
YOLO results. Update README and `.env.example` so the documented flow no
longer includes Fire heuristic or `FIRE_MODEL` configuration.

Run the complete test suite, compile checks and a five-second video benchmark.
The benchmark must show no `fire` label unless YOLO26n itself produces one.
