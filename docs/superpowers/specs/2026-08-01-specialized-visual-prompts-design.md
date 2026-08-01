# Specialized Visual Prompts Design

## Goal

Make the VLM independently classify visible event dynamics while the upstream router only decides whether to call it and which specialized prompt profile to use. Remove YOLO summaries, router evidence, and candidate names from all VLM prompt text.

## Responsibility Boundary

The pipeline responsibilities become:

```text
Motion / YOLO / specialized detectors
  -> aggregate inexpensive signals
  -> router emits a neutral candidate
  -> candidate selects a prompt profile
  -> VLM receives images plus specialized visual instructions only
  -> validated event_type determines alert severity
```

YOLO and router evidence remain internal for gating, queue priority, primary-candidate selection, proximity, keyframes, metrics, and decision linkage. They are not visual truth and must not be presented to Qwen as evidence.

The existing `candidate` object continues to flow through `analyze_with_trace` and decision creation, but only its type is inspected locally to select a prompt profile. No candidate field is interpolated into prompt content.

## Prompt Profiles

This iteration introduces two profiles.

### Traffic profile

Selected for:

- `vehicle_scene`;
- `person_vehicle_scene`.

The traffic prompt instructs Qwen to compare temporal states and classify visible traffic dynamics. It must state:

- compare the `TRUOC` and `SAU` states;
- select `traffic_accident` when vehicles change from separated to touching or overlapping, change direction abruptly, or stop in an abnormal relative position;
- the exact impact frame, visible damage, or a person falling is not required when the before/after transition supports a collision;
- select `person_vehicle_interaction` only when a person interacts with a vehicle without evidence of a collision;
- select `uncertain + unknown_event` only when image quality or occlusion prevents determining the visible change.

It must not assert that every nearby pair of vehicles is an accident; classification depends on the temporal transition.

### Generic profile

Selected for every other candidate and for calls without a candidate. It asks Qwen to compare the supplied images and classify only visible events using the same fixed taxonomy. It contains no router hypothesis or object-detector context.

Future work may add person-safety, fire/smoke, or camera-tamper profiles without changing the router, analyzer API, or output schema.

## Prompt Content

Both profiles retain only:

- the visual-analysis instruction;
- the allowed `decision` values;
- the allowed `event_type` taxonomy;
- consistency rules: `no + no_event`, `uncertain + unknown_event`, and `yes + concrete event`;
- a Vietnamese `summary` of one or two short sentences, targeting no more than 40 words;
- temporal panel guidance for composite or separate images.

Both profiles remove:

- `Nghi vấn: {candidate_type}`;
- `Bằng chứng router: {candidate_evidence}`;
- `Dữ liệu YOLO: {detections}`;
- the blanket warning not to conclude an accident merely because people and vehicles coexist;
- the blanket instruction that defaults insufficient evidence to uncertainty without defining what visual evidence is sufficient.

The existing strict candidate JSON schema remains unchanged:

```json
{
  "decision": "yes | no | uncertain",
  "event_type": "fixed taxonomy value",
  "summary": "one or two short Vietnamese sentences"
}
```

## Non-Candidate Calls

The legacy non-candidate prompt also stops embedding formatted detections. Its existing output schema (`alert_level`, `summary`, `risks`, `recommended_action`) remains unchanged to avoid broad API migration in this iteration.

The `detections` method argument remains for interface compatibility and fallback scene construction. Removing it from the public analyzer interface is out of scope.

## Selection Boundary

Prompt-profile selection remains a small pure function inside the Ollama Qwen module:

```text
candidate type in {vehicle_scene, person_vehicle_scene}
  -> traffic prompt
otherwise
  -> generic prompt
```

This boundary makes adding another profile a local mapping change rather than a new router or VLM interface.

## Failure Handling

HTTP, timeout, malformed JSON, taxonomy validation, degraded output, and `yes/no/uncertain` consistency behavior remain unchanged. Router evidence is still preserved in candidates and benchmark data; it is simply not sent to Qwen.

## Performance

The new prompts are shorter because detection counts and serialized evidence are removed. Image count, composite dimensions, context size, output-token limit, temperature, and number of requests remain unchanged. No latency increase is expected; prompt evaluation may decrease slightly.

## Testing

Payload tests will verify:

- traffic candidates select the traffic profile;
- a generic candidate selects the generic profile;
- candidate names, evidence keys/values, YOLO labels, `count=`, and confidence values are absent from prompt content;
- traffic rules about before/after contact and abnormal stopping are present;
- generic prompt does not contain traffic-specific rules;
- calls without a candidate do not embed detections;
- strict candidate and legacy output schemas remain unchanged;
- composite temporal instructions remain present;
- candidate linkage and trace output remain unchanged.

The full test suite will verify no routing, benchmark, alert, or pipeline regression. Automated tests will not call Ollama or rewrite benchmark JSON.

## Success Criteria

- Qwen prompt content is derived only from the selected visual prompt profile and image ordering.
- Router/YOLO data remain available internally but never appear in the outgoing prompt.
- Traffic candidates receive concise, positive criteria for `traffic_accident`.
- The request still contains one composite image, one Qwen call, and the same response schema.
- A later manual rerun of `RoadAccidents010_x264.mp4` can evaluate whether the corrected responsibility boundary improves event classification.
