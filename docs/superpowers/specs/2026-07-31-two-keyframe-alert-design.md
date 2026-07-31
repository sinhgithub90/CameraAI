# Two-Keyframe Alert Screening Design

## Goal

Reduce Qwen input from four frames to two for five-second alert screening,
while preserving the original video for human review after an alert.

## Selection

For an active window, select the observation with the highest combined
Motion/YOLO score. Select the second highest-scoring observation at least one
second away from the first; if none meets that separation, select the
temporally farthest observation. Return the pair in chronological order.
Calm observations retain first/last behavior, although calm video does not
normally invoke Qwen.

The default `SecurityAIPipeline.max_keyframes` becomes two. Explicit caller
overrides continue to work, and selection for limits above two is unchanged.

## Verification

Tests must fail against the old first/last two-frame behavior, verify event
frames and temporal separation, and prove the default video pipeline sends no
more than two frames to the VLM. Benchmark one warm Qwen request after the
change and report elapsed time without enforcing a timing assertion.
