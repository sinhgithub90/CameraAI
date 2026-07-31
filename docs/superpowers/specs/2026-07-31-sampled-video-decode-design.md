# Sampled Video Decode Optimization

## Goal

Reduce non-model video overhead without changing Motion, YOLO, keyframe or
Qwen inputs.

## Design

Replace the per-frame `VideoCapture.read()` loop with `grab()` for every frame
position and `retrieve()` only when the frame index matches the configured
Motion sampling interval. Successful grabs continue to count toward
`frames_read`, timestamps and five-second window limits, so temporal behavior
is unchanged. The most recently retrieved sampled frame is sufficient for the
skipped-video preview; active windows already choose their output from sampled
keyframes.

If `grab()` or `retrieve()` fails, preserve the existing end-of-video behavior.
No seeking is used because codec keyframe seeking can be slower and inaccurate.
No GPU decoder dependency is added.

## Verification

Add a fake capture regression test whose `read()` method raises, then assert
that a 30 FPS stream sampled at 5 FPS grabs all positions but retrieves only
sampled frames. Run all tests and benchmark a temporary 1080p 150-frame video,
reporting decode overhead rather than enforcing a timing threshold in tests.
