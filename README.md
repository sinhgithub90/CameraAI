# CameraAI

CameraAI analyzes camera images and videos with a lightweight computer-vision
router and a local Qwen vision-language model. The core package lives in
`src/camera_ai/`; FastAPI is an adapter for uploads, asynchronous processing,
and compact result polling.

## Current async video pipeline

The FastAPI video path reads the complete video and closes one source-time
window every five seconds:

```text
Video/stream
  -> close five-second source-time boundary
  -> pre-queue camera admission
       active cooldown -> drop before queue and all inference
       due recheck     -> atomically reserve one window
       normal          -> admit
  -> global priority queue
  -> motion sampling (default 5 FPS)
  -> YOLO26n sampling on active windows (default 2 FPS)
  -> scene-composition router
  -> at most two keyframes around the activity span
  -> traffic or generic Qwen prompt
  -> one Qwen call with one composite image
  -> decision + event_type + summary
  -> event severity and per-window result
```

Admitted static windows skip YOLO and Qwen and are written as green results. If
a window contains motion but YOLO finds no supported object, it can still route
to Qwen as `unexplained_motion`; this prevents YOLO from filtering out smoke,
obstruction, spills, or fallen objects that it does not classify.

After Qwen verifies a red security event, that camera/analysis enters a
60-second cooldown measured in video source time. Later five-second boundaries
are still read so the stream remains current, but they are rejected before a
`VLMTask`, compatibility alert, or per-window result is created. They therefore
do not enter the queue and do not run Motion, YOLO, routing, keyframe selection,
or Qwen. The analysis records their count and duration once in the top-level
`cooldown` summary instead of creating synthetic suppressed windows.

Red confirmation also removes already queued backlog for the same
`(analysis_id, camera_id)`. An atomic queue cutoff rejects a stale task that was
admitted just before red but arrives after pruning; work for other cameras is
untouched. The first boundary starting at or after the deadline atomically
reserves the only eligible recheck. Repeated red extends the episode by 60
seconds, orange rechecks after 15 seconds, green resolves it, and failed
rechecks keep the alert with bounded retry backoff. The processor retains a
second cooldown check only as defense in depth. The async API rejects a second
active analysis for the same `camera_id` with HTTP 409.

The router describes scene composition rather than claiming an event. Current
candidate types include `person_vehicle_scene`, `multi_person_scene`,
`person_scene`, `vehicle_scene`, `unexplained_motion`, and
`temporally_confirmed_fire_signal`. The candidate only selects a specialized
traffic or generic visual prompt. Candidate values, router evidence, YOLO
labels, counts, confidence values, and bounding boxes are not embedded in the
Qwen prompt.

For two-keyframe windows, the selector smooths temporal change over three
observations, finds the activity span at 30% of the smoothed peak while
tolerating one inactive sample, and selects context approximately 0.6 seconds
before and after that span. The default `composite` mode places the `TRUOC`
frame above the `SAU` frame in one 960x1080 image, so selection adds no extra
inference call.

Qwen returns only:

```json
{
  "decision": "yes",
  "event_type": "traffic_accident",
  "summary": "Một phương tiện va chạm với phương tiện khác."
}
```

Supported event types are `no_event`, `person_vehicle_interaction`,
`traffic_accident`, `person_fall`, `fighting`, `fire_smoke`, `camera_tamper`,
and `unknown_event`. A validated event type determines UI severity independently
of router priority:

- Green: `no_event`, `person_vehicle_interaction`, `unknown_event`
- Orange: `person_fall`, `camera_tamper`
- Red: `traffic_accident`, `fighting`, `fire_smoke`

`SecurityAIPipeline` keeps `max_video_windows=1` as a direct-construction
development default. The FastAPI runtime explicitly constructs it with
`max_video_windows=None`, so API video uploads process every five-second
window.

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m pip install -r requirements.txt
```

## Model setup

Runtime defaults:

```text
YOLO_WEIGHTS=yolo26n.pt
OLLAMA_MODEL=qwen3-vl:4b-instruct-q4_K_M
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_NUM_CTX=4096
OLLAMA_NUM_PREDICT=128
OLLAMA_KEEP_ALIVE=10m
OLLAMA_FRAME_MODE=composite
CAMERA_AI_VLM_POLICY=gated
CAMERA_AI_VLM=ollama
```

`yolo26n.pt` is downloaded automatically by Ultralytics on first use. The
default pipeline uses YOLO26n before Qwen; it does not load separate fire
weights. YOLO26 requires Ultralytics `8.4.0` or newer:

```powershell
python -m pip install --upgrade "ultralytics>=8.4.0"
ollama list
```

## Run the API

```powershell
$env:YOLO_WEIGHTS = "yolo26n.pt"
$env:OLLAMA_MODEL = "qwen3-vl:4b-instruct-q4_K_M"
$env:OLLAMA_NUM_CTX = "4096"
$env:OLLAMA_NUM_PREDICT = "128"
$env:OLLAMA_KEEP_ALIVE = "10m"
$env:OLLAMA_FRAME_MODE = "composite"
python -m uvicorn apps.api.main:app --reload
```

Main endpoints:

- `GET /health`: runtime health
- `POST /analyze/image`: synchronous image analysis
- `POST /analyze/video`: synchronous video analysis
- `POST /async/analyze/video`: enqueue complete video analysis
- `GET /analyses/{analysis_id}`: poll asynchronous results

Use `OLLAMA_FRAME_MODE=separate` only when comparing separate-image input with
the default composite input. Set `CAMERA_AI_VLM=mock` for API development
without Ollama; `CAMERA_AI_VLM_POLICY=gated` remains the normal runtime policy.

## Quick per-video benchmark

The benchmark CLI calls an already-running FastAPI server; it does not start
FastAPI or Ollama. To process one arbitrary video:

```powershell
python -m scripts.benchmark_pipeline `
  --input-file "D:\CongViec\CameraAI\CameraAI\videos\RoadAccidents010_x264.mp4" `
  --output-dir "D:\CongViec\CameraAI\CameraAI\runs\alerts"
```

Use `--input-dir` instead of `--input-file` to process a directory
sequentially. Each attempted video creates one `<video-stem>.json`; failed
videos also create a report with `status` and `error`. The CLI uses each video
stem as its `camera_id`.

Every completed report keeps the green, orange, and red windows that actually
entered processing. A compact processed window looks like:

```json
{
  "window_index": 1,
  "start_seconds": 5.0,
  "end_seconds": 10.0,
  "alert_level": "high",
  "qwen": {
    "status": "completed",
    "degraded": false,
    "verified": true,
    "reason": "candidate_requires_verification",
    "summary": "Xe buýt va chạm với xe ô tô."
  },
  "timing": {
    "motion_ms": 10.3,
    "detector_ms": 337.7,
    "keyframe_ms": 0.2,
    "qwen_ms": 3758.6,
    "total_ms": 4106.8,
    "queue_wait_ms": 46.0,
    "wall_clock_ms": 4156.0,
    "within_budget": true
  }
}
```

Dropped and pruned source windows are summarized once rather than appended to
`windows`:

```json
{
  "cooldown": {
    "active_alert_id": "alert_31cb0edeeef0fad5",
    "alert_level": "high",
    "timebase": "video",
    "red_started": 10.0,
    "recheck_at": 70.0,
    "suppressed_windows": 2,
    "suppressed_seconds": 10.0
  }
}
```

Confirmed alert severity takes precedence over the scene security level when
the internal pipeline resolves `alert_level`. The public analysis response and
per-video benchmark JSON omit detections, Qwen input frames, candidate data,
raw alerts, traces, risks, actions, and internal `event_metadata`. Empty
cooldown values and false episode transition flags are omitted.

The processing target is `total_ms <= 5000` for every processed window. The
top-level `performance_summary` reports Qwen call rate, p95 processing time,
and the number of processed windows over budget. Call and cooldown rates use
the logical count `len(windows) + cooldown.suppressed_windows`; p95 only uses
work that actually ran. `queue_wait_ms` is time before a worker starts,
`total_ms` is Motion + Detection + keyframe + Qwen processing, and
`wall_clock_ms` covers both. A video must continue for at least 60 seconds after
its first verified red window to contain an eligible recheck boundary.

## Tests

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q
```

See [docs/benchmarks/README.md](docs/benchmarks/README.md) for fixture and
benchmark-report details.
