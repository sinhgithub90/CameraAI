# CameraAI

## Model setup

The upload endpoints use these defaults:

```text
YOLO_WEIGHTS=yolo26n.pt
OLLAMA_MODEL=qwen3-vl:4b-instruct-q4_K_M
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_NUM_CTX=8192
```

`yolo26n.pt` is downloaded automatically by Ultralytics on first detection.
The FireDetector remains a separate fire/smoke trigger. The configured Qwen
model is already available in the local Ollama installation; verify it with:

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
$env:OLLAMA_NUM_CTX = "8192"
python -m uvicorn apps.api.main:app --reload
```

## Video motion-first pipeline

Video analysis uses a lightweight motion stage before the expensive detectors:

```text
Video frames -> Motion (default 5 FPS) -> YOLO/Fire (default 2 FPS)
             -> candidate/keyframe selection (max 8) -> one VLM call
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

Each window reports the keyframe indices sent to Qwen and stage timings in
`qwen_input` and `timing`; the same information is logged to the terminal.

The defaults can be overridden when constructing `SecurityAIPipeline` with
`motion_fps`, `yolo_fps`, and `max_keyframes`. Stage boundaries remain
framework-agnostic so the synchronous MVP can later move to worker queues.

> Camera-AI sub-module (YOLO11 + Qwen-VL): `src/camera_ai/` + `apps/api/`. Chi tiết bên dưới.

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
    yolo.py              # YOLO11 COCO (ultralytics), lazy-load singleton
    fire.py              # FireDetector — fire YOLO nhẹ + heuristic fallback
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
Input → YOLO11 (COCO) ──┐
                        ├─→ VLMGate: có trigger nào không?
       FireDetector ────┘      │
      (fire YOLO / heuristic)  ├─ Có  → Qwen-VL phân tích → PipelineResult đầy đủ
                               └─ Không → skip VLM → PipelineResult nhẹ (vlm.skipped=true)
```

VLM là tầng đắt — chỉ chạy khi tầng detect rẻ báo có tín hiệu (người/xe/lửa/khói). Với luồng camera sau này, thêm motion gate làm trigger 24/7.

## Cấu hình qua env

| Biến | Mặc định | Ý nghĩa |
|---|---|---|
| `OLLAMA_MODEL` | `qwen3-vl:2b-instruct-q8_0` | Model VLM trên Ollama (bạn bè dùng model Qwen khác thì đổi cái này) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Endpoint Ollama |
| `CAMERA_AI_VLM_POLICY` | `gated` | `gated`: chỉ gọi VLM khi có trigger · `always`: gọi mọi input |
| `CAMERA_AI_VLM` | `ollama` | `mock`: dùng VLM giả, không cần Ollama |
| `FIRE_MODEL` | (URL fire YOLO11n) | Path/URL model fire/smoke · `none`/`off`: tắt tầng fire |

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

- `yolo11n.pt` và model fire được tự tải về khi có request đầu tiên.
- Ollama phải đang chạy với model vision, VD: `ollama pull qwen3-vl:2b-instruct-q8_0`.
