# Current Pipeline README Refresh Design

## Goal

Make the repository documentation match the pipeline and benchmark JSON that are currently implemented. This work changes documentation only.

## Root README

Keep the existing setup and API startup instructions, then describe the active async video flow accurately:

1. Split the complete API-uploaded video into five-second windows.
2. Sample motion at 5 FPS and YOLO26n at 2 FPS.
3. Skip static windows; route active windows with scene-composition candidates.
4. Select at most two keyframes around the detected activity span.
5. Use the candidate only to select a traffic or generic Qwen prompt profile.
6. Send one composite image to one Qwen call by default.
7. Parse only `decision`, `event_type`, and `summary` from Qwen.
8. Map the validated event type to green, orange, or red independently of router priority.

Clarify that the `SecurityAIPipeline` constructor retains a one-window development default, while the FastAPI runtime explicitly uses `max_video_windows=None` and processes the complete video.

Add the supported event types, severity mapping, async endpoint workflow, quick single-video CLI command, compact JSON shape, and five-second processing target. State that detection boxes and detection summaries are not written into the per-video benchmark JSON.

## Benchmark README

Preserve corpus and historical smoke-baseline documentation. Replace the outdated alert CLI section so it documents the current per-video output:

- every window is written, including green windows;
- confirmed `event_metadata.alert.severity` overrides the scene security level;
- each window contains `candidate_type`, compact `qwen`, and detailed `timing` objects;
- timing retains motion, detector, keyframe, Qwen, total, queue wait, wall clock, and `within_budget`;
- `detection_summary`, raw boxes, candidate evidence, IDs, and verbose decision/routing objects are omitted;
- top-level level and performance summaries remain available.

Retain both single-file and directory CLI examples and the requirement that FastAPI and Ollama already be running.

## Validation

Validate all documented CLI flags against `python -m scripts.benchmark_pipeline --help`, scan both README files for removed schema names presented as current behavior, run `git diff --check`, and run the focused benchmark CLI tests because the documentation describes their external contract.
