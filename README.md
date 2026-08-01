# CameraAI

## Model setup

The upload endpoints use these defaults:

```text
YOLO_WEIGHTS=yolo26n.pt
OLLAMA_MODEL=qwen3-vl:4b-instruct-q4_K_M
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_NUM_CTX=4096
OLLAMA_NUM_PREDICT=128
OLLAMA_KEEP_ALIVE=10m
OLLAMA_FRAME_MODE=composite
```

`yolo26n.pt` is downloaded automatically by Ultralytics on first detection.
The runtime pipeline uses only YOLO26n before Qwen; no fire heuristic or
separate fire weights are loaded. The configured Qwen model is already
available in the local Ollama installation; verify it with:

```powershell
ollama list
```

YOLO26 requires Ultralytics `8.4.0` or newer. If an existing environment
still reports an older version, upgrade it with:

```powershell
python -m pip install --upgrade "ultralytics>=8.4.0"
```

To run the upload API:

```powershell
$env:YOLO_WEIGHTS = "yolo26n.pt"
$env:OLLAMA_MODEL = "qwen3-vl:4b-instruct-q4_K_M"
$env:OLLAMA_NUM_CTX = "4096"
$env:OLLAMA_NUM_PREDICT = "128"
$env:OLLAMA_KEEP_ALIVE = "10m"
$env:OLLAMA_FRAME_MODE = "composite"
python -m uvicorn apps.api.main:app --reload
```

## Video motion-first pipeline

Video analysis uses a lightweight motion stage before the expensive detectors:

```text
Video frames -> Motion (default 5 FPS) -> YOLO26n (default 2 FPS)
             -> before/change keyframe selection (max 2) -> one VLM call
```

Static video skips YOLO and VLM. If motion is detected but YOLO finds no
objects, the keyframes are still sent to the VLM so smoke, fire, obstruction,
spills, or fallen objects are not filtered out by object detection.
`PipelineResult.video_stats` exposes frame and keyframe counters.

The current test mode reads only the first five-second window by default
(`max_video_windows=1`). Set `max_video_windows=None` when constructing the
pipeline to process the complete video. Frames are grouped into five-second
windows (`window_seconds=5.0`), and each active window produces one VLM
analysis in `PipelineResult.video_windows`.

For two-keyframe windows, the selector smooths change scores across three
observations, expands an activity span at 30% of the smoothed peak while
tolerating one inactive sample, then selects context 0.6 seconds before the
span and 0.6 seconds after it. It reports the selected indices
and stage timings in `qwen_input` and `timing`; the same information is logged
to the terminal. By default, two keyframes are fitted into a single 960x1080
image: `TRUOC` on top and `SAU` below. Router candidates select a traffic or
generic visual prompt profile; candidate values, router evidence, and YOLO
summaries stay internal. Qwen is constrained by an Ollama JSON schema to return
only `decision`, `event_type`, and a short `summary`. Set
`OLLAMA_FRAME_MODE=separate` to send the two keyframes as separate images.
Selection remains inside the current five-second window and adds no inference
call.

The defaults can be overridden when constructing `SecurityAIPipeline` with
`motion_fps`, `yolo_fps`, and `max_keyframes`. Stage boundaries remain
framework-agnostic so the synchronous MVP can later move to worker queues.

> Camera-AI sub-module (YOLO26n + Qwen-VL): `src/camera_ai/` + `apps/api/`. Chi tiết bên dưới.

---

# Camera AI Sub-module

Core module phân tích camera an ninh: **YOLO detect object** + **Qwen-VL phân tích ngữ cảnh an ninh** (chạy qua Ollama local). FastAPI chỉ là lớp demo adapter — mọi logic nằm trong `camera_ai`, không phụ thuộc web framework, sau này dùng chung cho queue worker được.

## Cấu trúc

```
src/camera_ai/
  schemas.py             # EventObject, Detection, SceneAnalysis, PipelineResult...
  pipeline.py            # SecurityAIPipeline — entry point chính (không import FastAPI)
  gate.py                # VLMGate — quyết định có gọi VLM hay không (gated | always)
  detectors/
    base.py              # Detector interface (pluggable)
    yolo.py              # YOLO26n COCO (ultralytics), lazy-load singleton
    fire.py              # FireDetector standalone (không dùng trong pipeline mặc định)
  vlm/
    base.py              # VLMAnalyzer interface (pluggable)
    ollama_qwen.py       # Qwen-VL qua Ollama /api/chat (model configurable)
    mock.py              # Fallback deterministic — test API khi không có Ollama
apps/api/
  main.py                # FastAPI: GET / (UI), POST /analyze/image, POST /analyze/video, GET /health
  static/index.html      # Giao diện web nhẹ: upload ảnh/video, xem bbox + kết quả
examples/demo_client.py  # Upload ảnh test
tests/                   # Smoke test core (không cần model/Ollama)
```

## Luồng xử lý

```
Input → YOLO26n (COCO) → VLMGate: có detection nào không?
                              ├─ Có  → Qwen-VL phân tích → PipelineResult đầy đủ
                              └─ Không → skip VLM → PipelineResult nhẹ (vlm.skipped=true)
```

VLM là tầng đắt — chỉ chạy khi tầng detect rẻ báo có tín hiệu (người/xe/lửa/khói). Với luồng camera sau này, thêm motion gate làm trigger 24/7.

## Cấu hình qua env

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `OLLAMA_MODEL` | `qwen3-vl:4b-instruct-q4_K_M` | Model VLM trên Ollama (bạn bè dùng model Qwen khác thì đổi cái này) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Endpoint Ollama |
| `OLLAMA_NUM_CTX` | `4096` | Context phù hợp để Qwen 4B nằm hoàn toàn trên GPU 6 GB |
| `OLLAMA_NUM_PREDICT` | `128` | Đủ chỗ cho candidate JSON chỉ gồm decision và summary ngắn |
| `OLLAMA_KEEP_ALIVE` | `10m` | Giữ model trong Ollama giữa các lần test |
| `OLLAMA_FRAME_MODE` | `composite` | `composite`: ghép hai keyframe thành một ảnh; `separate`: gửi hai ảnh riêng |
| `CAMERA_AI_VLM_POLICY` | `gated` | `gated`: chỉ gọi VLM khi có trigger · `always`: gọi mọi input |
| `CAMERA_AI_VLM` | `ollama` | `mock`: dùng VLM giả, không cần Ollama |

## Cài đặt

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows (PowerShell)
pip install -e .                # cài package camera_ai từ src/
pip install -r requirements.txt
```

## Chạy API

```bash
python -m uvicorn apps.api.main:app --reload
```

- `GET  /`            — giao diện web demo (upload file, xem kết quả)
- `GET  /health`
- `POST /analyze/image`  — multipart: `file` (ảnh), `camera_id` (optional)
- `POST /analyze/video`  — multipart: `file` (video), `camera_id` (optional)

Mở trình duyệt `http://127.0.0.1:8000/` để dùng UI.

Test nhanh:

```bash
python examples/demo_client.py path/to/frame.jpg cam_front_gate
```

Chạy không cần Ollama (dùng mock VLM, kết quả có `degraded: true`):

```bash
set CAMERA_AI_VLM=mock
python -m uvicorn apps.api.main:app --reload
```

## Test core

```bash
python -m pytest tests/ -v
```

## Điểm cần model (chạy lần đầu)

- `yolo26n.pt` được tự tải về khi có request đầu tiên.
- Ollama phải đang chạy với model vision: `qwen3-vl:4b-instruct-q4_K_M`.
Async video uses a conservative Qwen gate: fully static five-second windows are
written as green results without a VLM call, while motion/object/fire candidates
still receive one candidate-aware Qwen verification. Benchmark JSON reports the
Qwen call rate and whether processing stays within 5 seconds per window.

Candidate-aware Qwen responses contain `decision`, `event_type`, and `summary`.
`event_type` is selected from `no_event`, `person_vehicle_interaction`,
`traffic_accident`, `person_fall`, `fighting`, `fire_smoke`, `camera_tamper`, or
`unknown_event`. The router candidate selects the prompt profile but is not
embedded in prompt text or treated as the final event classification. Traffic
prompts classify temporal vehicle contact and abnormal position changes
directly from the images.

Router candidates describe scene composition and decide whether Qwen runs:
`person_vehicle_scene`, `multi_person_scene`, `person_scene`, `vehicle_scene`,
`unexplained_motion`, or the specialized `temporally_confirmed_fire_signal`.
Validated Qwen `event_type` determines the final UI severity independently of
router priority: `no_event`, `person_vehicle_interaction`, and `unknown_event`
are green; `person_fall` and `camera_tamper` are orange; `traffic_accident`,
`fighting`, and `fire_smoke` are red.
