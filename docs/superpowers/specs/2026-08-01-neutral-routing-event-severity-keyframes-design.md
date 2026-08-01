# Neutral Routing, Event Severity, and Temporal Keyframes Design

## Goal

Improve Qwen's event classification without adding object tracking or another computer-vision model. The change must keep each five-second window within the existing latency budget and preserve the current one-call-per-window VLM architecture.

## Scope

This iteration contains exactly three changes:

1. Replace event-like router candidate names with neutral scene descriptions.
2. Derive the final alert severity from Qwen's validated `event_type` rather than the router candidate priority.
3. Send Qwen two temporally meaningful keyframes representing context before a change and the strongest change itself.

It does not add tracking, trajectory analysis, new detectors, bbox output, additional Qwen calls, or a new event taxonomy.

## Neutral Router Candidates

The router remains a cheap gate based on aggregated YOLO detections and motion. It decides whether Qwen should run and supplies scene context, but it must not suggest that an event has already occurred.

Candidate names change as follows:

| Current candidate | New candidate |
| --- | --- |
| `possible_person_vehicle_interaction` | `person_vehicle_scene` |
| `multi_person_high_motion` | `multi_person_scene` |
| `person_only_activity` | `person_scene` |
| `vehicle_only_activity` | `vehicle_scene` |
| `unknown_motion` | `unexplained_motion` |

The temporally confirmed fire signal remains specialized because it represents a detector-backed signal, but its candidate name must describe a signal rather than assert the final event: `temporally_confirmed_fire_signal`.

Routing order, evidence aggregation, proximity calculation, priority, and the rule that static empty windows skip Qwen remain unchanged. Candidate priority is retained only for queue ordering and candidate selection; it no longer determines the final UI severity.

## Event-Type Severity Policy

An alert is created only when the model decision is `yes` and the raw Qwen response is valid. Its severity is mapped from the validated event taxonomy:

| `event_type` | Severity / UI color |
| --- | --- |
| `no_event` | low / green |
| `person_vehicle_interaction` | low / green |
| `unknown_event` | low / green |
| `person_fall` | medium / orange |
| `camera_tamper` | medium / orange |
| `traffic_accident` | high / red |
| `fighting` | high / red |
| `fire_smoke` | high / red |

Although valid model-output consistency normally prevents `decision=yes` with `no_event` or `unknown_event`, the mapping explicitly covers those values so the policy remains deterministic for programmatic callers and future changes.

Unknown or missing event types fall back to low severity. They must never inherit a medium or high severity from the router candidate.

## Two-Frame Temporal Selection

The existing selector chooses two independently high-scoring frames. That can produce two similar images from the same state. The new two-frame mode instead selects an ordered temporal pair.

Calculate a change score for every observation. The first observation is compared
with an empty baseline; every later observation is compared with the immediately
previous sampled observation. Each score uses:

- the current motion score;
- the absolute change in person count from the previous sampled observation;
- the absolute change in vehicle count from the previous sampled observation;
- whether the set of detected labels changed.

The observation with the highest change score is the event frame. The context frame is the latest earlier observation at least one second before it. If no such observation exists, use the earliest earlier observation. Return the pair in chronological order.

Fallback behavior is deterministic:

- If the strongest change occurs at the first observation, pair it with the latest observation.
- If all change scores are zero, use the first and last observations.
- If only one observation exists, return that observation once.
- Existing selection behavior for `max_keyframes` values other than two remains unchanged.

The algorithm uses detections already produced by YOLO and performs only linear in-memory comparisons. It adds no inference call and should have negligible effect on the five-second processing budget.

## Data Flow

For each five-second window:

1. Motion and YOLO observations are aggregated as today.
2. The router emits a neutral scene candidate or skips Qwen for an empty static window.
3. The keyframe selector produces a context/change pair.
4. Qwen receives the neutral candidate and the two frames, then returns `decision`, `event_type`, and `summary`.
5. A valid `yes` decision creates an alert whose severity comes exclusively from `event_type`.
6. Benchmark JSON continues to expose statistics and compact VLM output without bbox data.

## Compatibility and Failure Handling

- Candidate identifiers change because they include `candidate_type`; callers and tests must expect the new deterministic identifiers.
- Existing benchmark JSON structure is unchanged apart from candidate string values and potentially improved selected-frame indices.
- Invalid, malformed, or inconsistent Qwen responses keep the existing degraded/uncertain handling and cannot create an alert.
- No-event and uncertain windows remain represented in benchmark output according to the current benchmark policy.

## Testing

Tests will cover:

- every router rule emitting its new neutral candidate name;
- unchanged router priority and Qwen-call gating behavior;
- every taxonomy value mapping to the intended severity;
- unknown event types falling back to low severity;
- invalid or non-`yes` decisions producing no alert;
- keyframe selection for a middle change, early change, no change, and one observation;
- chronological ordering and a maximum of two selected frames;
- pipeline integration showing that candidate priority cannot override event-type severity;
- the existing full test suite to detect output-contract regressions.

## Success Criteria

- Qwen is no longer prompted with router labels that imply an accident or interaction conclusion.
- A validated `traffic_accident` becomes red even when the router candidate priority is low or medium.
- A benign `person_vehicle_interaction` stays green even when proximity made the router candidate medium.
- The two images sent to Qwen represent temporal context and change whenever the window contains such a pair.
- No additional model inference is introduced, and benchmark timing remains within the existing five-second steady-state target.
