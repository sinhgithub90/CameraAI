# Event-Span Keyframes Design

## Goal

Select two Qwen frames around the complete visible activity associated with the strongest change, rather than around one instantaneous peak. The selector should capture a stable state before activity and a resulting state after activity without increasing inference work.

## Scope

This iteration changes only the `max_keyframes == 2` selection strategy in `src/camera_ai/video_selection.py`, plus tests and current documentation. It does not change:

- video decoding or sample rates;
- motion detection or YOLO inference;
- `_observation_change_score` inputs or weights;
- router candidates, VLM policy, prompt, or output taxonomy;
- the number, dimensions, or composition of images sent to Qwen;
- five-second window boundaries or cross-window state;
- behavior for keyframe limits other than two.

## Activity Signal

Calculate the existing raw change score for every observation. Smooth the scores with a centered three-sample moving mean:

```text
smoothed[i] = mean(raw[max(0, i - 1) : min(n, i + 2)])
```

At the first and last observations, average only the available two samples. This smoothing bridges short gaps caused by YOLO running at a lower sampling rate than motion analysis.

If every raw change score is zero, return the first and last observations as today. Otherwise, select the earliest observation with the highest smoothed score as the peak.

## Event Span

Define the active threshold as:

```text
active_threshold = 0.30 * peak_smoothed_score
```

Starting from the peak, scan independently toward the beginning and end of the window:

- a sample at or above the threshold is active and updates the span boundary;
- one consecutive below-threshold sample is tolerated as a gap;
- two consecutive below-threshold samples stop expansion;
- tolerated gaps bridge active samples but do not become the final span boundary.

The resulting `event_start` and `event_end` are therefore the first and last active observations connected to the peak with no run of two inactive observations.

## Frame Selection

Use fixed context offsets for this experiment:

- before target: `event_start.timestamp_seconds - 0.6`;
- after target: `event_end.timestamp_seconds + 0.6`.

Choose the observation nearest each target, constrained to be strictly before `event_start` for the before frame and strictly after `event_end` for the after frame. Equal-distance ties select the earlier timestamp.

Fallbacks are deterministic:

- if no observation precedes `event_start`, use the first observation;
- if no observation follows `event_end`, use the last observation;
- one observation returns once;
- two observations return both in input order;
- no activity returns the first and last observations;
- returned frames remain unique and chronological whenever the input contains at least two observations.

The selection stays inside the current five-second window. An event that reaches a window boundary therefore uses that boundary; cross-window buffering remains out of scope.

## Data Flow and Cost

```text
existing raw change scores
  -> centered 3-sample smoothing
  -> strongest smoothed peak
  -> contiguous activity span with one-gap tolerance
  -> before event_start / after event_end frames
  -> existing two-panel composite
  -> one Qwen call
```

All new operations are linear scans over approximately 25 observations per window. They add no image decoding, inference, encoding, or request payload. Expected selector overhead is negligible compared with YOLO and Qwen latency.

## Diagnostic Artifacts

No new artifact system is required. `WindowArtifactWriter` already saves `selected_frame_01.jpg` and `selected_frame_02.jpg` when the pipeline is constructed with `artifact_dir`. The implementation will preserve this behavior so selected before/after frames can be inspected after a benchmark run.

## Testing

Unit tests will cover:

- exact three-sample smoothing at the first, middle, and last observation;
- expansion across one inactive gap;
- stopping at two consecutive inactive observations;
- a middle event span selecting frames before its start and after its end;
- an event span touching the first or last observation;
- a detection-only signal;
- no activity, one observation, and two observations;
- chronological uniqueness;
- unchanged behavior for `max_keyframes != 2`.

Integration tests will continue to assert that a routed video window sends no more than two frames in one VLM call.

## Evaluation

Automated verification will not run Ollama or rewrite benchmark outputs. After implementation, a manual rerun of `RoadAccidents010_x264.mp4` should be inspected for:

- a wider pair around the visible event, ideally approximately `6.x -> 9.xs`;
- whether the second frame shows the resulting vehicle positions;
- any change from `person_vehicle_interaction` to a more specific valid event;
- Qwen latency and the count of windows below 5,000 ms.

The code-level success criterion is correct event-span selection with no additional inference. Improved event classification remains an empirical benchmark outcome, not a guaranteed result.
