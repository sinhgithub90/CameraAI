# Before/After Peak Keyframes Design

## Goal

Give Qwen a visible state transition around the strongest event signal in each five-second window. Replace the current context/peak pair with a before/after pair while keeping two images, one Qwen call, and negligible selector cost.

## Scope

This change affects only the `max_keyframes == 2` branch in `src/camera_ai/video_selection.py` and its tests/documentation. It does not change:

- motion or detection sampling rates;
- change-score calculation;
- router candidates or Qwen gating;
- prompt, response taxonomy, or severity mapping;
- composite image size;
- the selector used for keyframe limits other than two;
- window boundaries or cross-window buffering.

## Selection Algorithm

Calculate the existing change score for every observation and select the earliest observation with the maximum score as `peak`.

When the maximum score is greater than zero:

1. Set the before target to `peak.timestamp_seconds - 1.0`.
2. Among observations strictly before the peak, choose the timestamp nearest to the before target. Resolve equal-distance ties in favor of the earlier timestamp.
3. Set the after target to `peak.timestamp_seconds + 0.8`.
4. Among observations strictly after the peak, choose the timestamp nearest to the after target. Resolve equal-distance ties in favor of the earlier timestamp.
5. Return the selected observations in chronological order.

The peak observation itself is not returned when both temporal sides exist. This deliberately shows Qwen the state before the strongest change and the resulting state after it.

The offsets are internal constants for this iteration. They are not exposed as configuration until benchmark evidence shows that tuning them per deployment is necessary.

## Fallbacks

- No observations or a non-positive keyframe limit returns an empty list, as today.
- One observation returns that observation once.
- Two observations return both in input order.
- If every change score is zero, return the first and last observations.
- If the peak has no earlier observation, use the first observation as the before frame and select the after frame normally.
- If the peak has no later observation, select the before frame normally and use the last observation as the after frame.
- Returned frames must be unique and chronological whenever at least two observations exist.

These fallbacks keep selection inside the current five-second window. Carrying frames across adjacent windows is explicitly deferred because it changes producer/processor state and is not needed to test this hypothesis.

## Expected Data Flow

```text
sampled observations
  -> existing motion/detection change scores
  -> strongest change peak
  -> nearest frame to peak - 1.0 s
  -> nearest frame to peak + 0.8 s
  -> existing TRUOC/SAU composite
  -> one Qwen request
```

No extra decoding, YOLO inference, image, or Qwen request is introduced. The selector remains linear in the number of sampled observations.

## Testing

Unit tests will use timestamps derived independently from the selector and cover:

- a middle peak selecting frames near `peak - 1.0s` and `peak + 0.8s`;
- a peak at the first observation;
- a peak at the last observation;
- a detection-only change without motion;
- no change;
- one and two observations;
- chronological uniqueness;
- unchanged behavior when `max_keyframes` is not two.

Existing video integration tests must continue to prove that only two frames are passed to one VLM call.

## Benchmark Evaluation

Implementation verification uses automated tests only. A later manual rerun of `RoadAccidents006_x264.mp4` should compare:

- selected timestamps around suspected accident windows;
- count of `yes + traffic_accident` versus `uncertain + unknown_event`;
- Qwen latency and windows within the 5,000 ms processing budget.

Success for the code change means the selector emits the specified before/after pair without adding inference work. Improved accident classification is a benchmark hypothesis, not guaranteed by the selector alone.
