# Camera AI Pipeline

Tài liệu này mô tả đúng trạng thái hiện tại của pipeline trong
`src/camera_ai/`. FastAPI chỉ là lớp nhận/trả HTTP; toàn bộ logic phân tích nằm
trong `SecurityAIPipeline`.

## 1. Thành phần đang sử dụng

- Motion detector bằng OpenCV để lọc cửa sổ video tĩnh.
- YOLO26n COCO để nhận diện đối tượng.
- Bộ chọn tối đa hai keyframe theo Motion, YOLO và vị trí thời gian.
- Qwen3-VL 4B chạy qua Ollama để đánh giá ngữ cảnh an ninh.
- JSON Schema để giữ output Qwen ngắn và có cấu trúc.

Runtime mặc định chỉ khởi tạo một detector là YOLO26n. `FireDetector` vẫn còn
trong mã nguồn để thử nghiệm độc lập nhưng không tham gia `SecurityAIPipeline`;
pipeline không tải `weights/best.pt`. Khói, lửa, té ngã hoặc vật cản ngoài tập
nhãn COCO được Qwen đánh giá trực tiếp từ keyframe.

## 2. Luồng video hiện tại

```text
Video upload/path
  |
  |-- Mặc định chỉ đọc cửa sổ 5 giây đầu
  |
  |-- grab từng frame để giữ đúng chỉ số và timestamp
  |     `-- chỉ retrieve/decode ở nhịp Motion 5 FPS
  |
  |-- resize cạnh dài tối đa 1280 (INTER_LINEAR)
  |
  |-- MotionDetector so sánh với frame Motion trước đó
  |     |-- cửa sổ không có motion -> bỏ qua YOLO và Qwen
  |     `-- có motion -> cho phép chạy YOLO tối đa 2 FPS
  |
  |-- chọn tối đa 2 keyframe theo điểm Motion/YOLO
  |
  |-- ghép TRUOC/SAU thành 1 ảnh và gọi Qwen một lần
  |
  `-- tạo VideoWindowResult, VideoAnalysisStats và PipelineResult
```

`frames_read` vẫn đếm mọi frame đã `grab`, nhưng pipeline không giải mã ảnh BGR
cho mọi frame. Ví dụ video 30 FPS trong 5 giây có 150 frame: pipeline `grab` 150
lần nhưng thường chỉ `retrieve` khoảng 25 frame ở nhịp Motion 5 FPS.

Mặc định `max_video_windows=1`, vì vậy endpoint upload hiện chỉ phân tích tối đa
5 giây đầu. Muốn xử lý toàn bộ video phải khởi tạo
`SecurityAIPipeline(max_video_windows=None)`; hiện chưa có biến môi trường hoặc
tham số HTTP để đổi giá trị này.

## 3. Điều kiện gọi YOLO và Qwen trong video

| Trạng thái cửa sổ 5 giây | YOLO26n | Qwen |
|---|---:|---:|
| Không có motion | Không gọi | Không gọi |
| Có motion | Tối đa 2 FPS | Gọi 1 lần |
| Có motion, YOLO không có detection | Có gọi | Vẫn gọi 1 lần |
| YOLO gặp lỗi ở một frame | Ghi log và tiếp tục | Vẫn giữ đường gọi Qwen |

`VLMGate` không được dùng trong luồng video. Quyết định gọi Qwen của video dựa
trên Motion: chỉ cần cửa sổ có ít nhất một frame báo motion thì Qwen được gọi,
kể cả danh sách detection rỗng. Cách này giảm nguy cơ bỏ sót sự kiện không có
nhãn COCO như khói, lửa, té ngã hoặc vật cản.

Với camera luôn có chuyển động, Qwen có thể được gọi một lần ở mỗi cửa sổ 5
giây. Pipeline hiện chưa có cooldown, tracking hay dedup cảnh báo giữa các cửa
sổ.

## 4. Motion và YOLO26n

### MotionDetector

Mặc định:

- lấy mẫu 5 FPS;
- chuyển ảnh sang grayscale và Gaussian blur `5x5`;
- ngưỡng sai khác pixel: `25`;
- tỷ lệ pixel thay đổi tối thiểu: `0.02` (2%);
- vùng thay đổi tối thiểu: 16 pixel vuông.

Frame Motion đầu tiên chỉ dùng làm mốc so sánh nên không tự tạo motion event.
Trạng thái Motion được reset khi bắt đầu phân tích mỗi video.

### YOLO26n

- Weights mặc định: `yolo26n.pt`.
- Confidence threshold mặc định: `0.35`.
- Model được lazy-load ở lần detect đầu và cache trong process.
- Nếu weights chưa có, Ultralytics tự tải ở lần chạy đầu tiên.
- Trong video, YOLO chỉ chạy trên frame có motion và bị giới hạn tối đa 2 FPS.
- Trong ảnh tĩnh, YOLO luôn chạy một lần trước khi `VLMGate` quyết định gọi Qwen.

Detection đầy đủ gồm `label`, `confidence`, `bbox` và `source` được giữ trong
output pipeline. Prompt Qwen không gửi bounding box; các detection trong cửa sổ
được rút gọn theo từng nhãn thành:

```text
- person: count=2, max_conf=0.91
- car: count=3, max_conf=0.88
```

`count` là số detection đã thu được trong cửa sổ, không phải số object duy nhất
đã tracking. Vì pipeline chưa có tracking, cùng một object có thể được đếm lại
ở nhiều frame YOLO.

## 5. Chọn keyframe

Mặc định mỗi cửa sổ có motion chọn tối đa hai keyframe.

Điểm xếp hạng của một frame gồm:

```text
score = motion_score * 0.6
      + confidence YOLO cao nhất * 0.3
      + 0.15 nếu là frame đầu hoặc cuối trong tập quan sát
```

Với cấu hình hai keyframe:

1. Chọn frame sự kiện có điểm cao nhất làm frame chính.
2. Ưu tiên frame sự kiện tiếp theo cách frame chính ít nhất 1 giây.
3. Nếu không có, chọn frame bất kỳ cách ít nhất 1 giây.
4. Nếu vẫn không có, chọn frame xa frame chính nhất.
5. Sắp xếp hai frame lại theo thứ tự thời gian trước khi gửi Qwen.

`qwen_input.frame_indices` và `qwen_input.timestamps_seconds` lưu đúng hai mốc
đã chọn trong output.

## 6. Input gửi Qwen

Mặc định `OLLAMA_FRAME_MODE=composite`. Khi có đúng hai keyframe:

- mỗi frame được fit vào panel đen `960x540`, giữ nguyên tỷ lệ;
- ảnh lớn được giảm bằng `INTER_LINEAR`, ảnh nhỏ không bị phóng lớn;
- panel trên có nhãn `TRUOC`, panel dưới có nhãn `SAU`;
- hai panel được ghép dọc thành một ảnh BGR `960x1080`;
- ảnh ghép được JPEG encode ở quality 85 và gửi dưới dạng base64;
- prompt nói rõ nửa trên là trước, nửa dưới là sau.

Như vậy pipeline vẫn chọn hai frame nhưng Ollama chỉ nhận một image trong
request. Đặt `OLLAMA_FRAME_MODE=separate` để gửi lại hai ảnh riêng. Chế độ
composite chỉ áp dụng khi đầu vào có đúng hai frame; input một frame hoặc số
frame khác hai giữ nguyên số ảnh.

Qwen nhận:

- ảnh hoặc ảnh ghép;
- danh sách nhãn YOLO đã rút gọn thành `count` và `max_conf`;
- yêu cầu trả cảnh báo an ninh ngắn bằng tiếng Việt;
- `temperature=0`, context 4096 và tối đa 128 output token.

JSON Schema yêu cầu đúng bốn trường:

```json
{
  "alert_level": "low | medium | high",
  "summary": "Mô tả ngắn",
  "risks": [],
  "recommended_action": "Hành động đề xuất"
}
```

`observations` trong API không còn được yêu cầu từ Qwen; pipeline tự tạo từ
`summary` để giữ tương thích schema cũ.

Nếu không kết nối được Ollama hoặc request lỗi, pipeline không làm hỏng toàn bộ
request mà trả `degraded=true`, kèm cảnh báo fallback dựa trên detection hiện
có. Nếu Ollama trả text không parse được thành JSON, text được giữ làm summary
và hệ thống ghi warning.

## 7. Luồng ảnh tĩnh

```text
Ảnh upload/path
  `-- decode và resize cạnh dài tối đa 1280 (INTER_AREA)
       `-- YOLO26n chạy một lần
            `-- VLMGate
                 |-- gated + có detection -> gọi Qwen với 1 ảnh
                 |-- gated + không detection -> bỏ qua Qwen
                 `-- always -> luôn gọi Qwen
```

Policy mặc định của ảnh tĩnh là `CAMERA_AI_VLM_POLICY=gated`. Khi Qwen bị bỏ
qua, output có `vlm.skipped=true` và `alert_level=low`.

## 8. Cách tổng hợp kết quả video

Mỗi cửa sổ có motion tạo một `VideoWindowResult` gồm:

- khoảng thời gian cửa sổ;
- toàn bộ detection thu được trong cửa sổ;
- kết quả VLM và quyết định an ninh;
- số keyframe và các frame/timestamp đã gửi Qwen;
- timing Motion, detector và Qwen.

Nếu phân tích nhiều cửa sổ, `PipelineResult` dùng cửa sổ có mức cảnh báo cao
nhất làm summary/security tổng. Ảnh minh họa là frame đại diện của cửa sổ đó,
được vẽ bounding box và JPEG encode quality 80. Danh sách detection cấp cao
nhất là tổng detection của mọi cửa sổ đã phân tích.

Nếu không cửa sổ nào có motion, pipeline trả kết quả skipped mức `low`, không
gọi YOLO/Qwen, nhưng vẫn trả ảnh đại diện cuối cùng đã decode.

## 9. Output và timing

Các trường chính của `PipelineResult`:

- `request_id`, `media_type`, `camera_id`;
- `detections`;
- `vlm.summary`, `vlm.observations`, `vlm.degraded`, `vlm.skipped`;
- `security.alert_level`, `security.risks`, `security.recommended_action`;
- `annotated_image` là JPEG base64;
- `video_stats` cho video;
- `video_windows` cho các cửa sổ có motion đã gọi Qwen.

`video_stats` gồm:

- bộ đếm: `frames_read`, `motion_frames`, `detector_frames`, `keyframes`,
  `windows_processed`, `windows_with_motion`, `vlm_calls`;
- stage timing: `motion_ms`, `detector_ms`, `qwen_ms`;
- overhead: `video_open_ms`, `frame_grab_ms`, `frame_retrieve_ms`,
  `frame_resize_ms`, `window_overhead_ms`, `untracked_ms`;
- tổng: `total_ms`.

Log terminal chính:

```text
Read: 150 frames | windows: 1 | Qwen calls: 1 | total: 5599.9ms
overhead | open 70.9ms, grab 113.3ms, retrieve 116.9ms, resize 146.3ms, window 0.6ms, untracked 30.1ms
window 0 [0-5s] | Qwen frames: 24, 78 | labels: car, person | motion 118.3ms, detector 819.9ms, Qwen 4183.7ms
```

Adapter Qwen ghi thêm:

```text
[qwen-input] frame_mode=composite source_frames=2 sent_images=1 composite_shape=960x1080
[ollama] total_ms=... load_ms=... prompt_tokens=... prompt_ms=... output_tokens=... output_ms=...
```

`qwen_ms` là thời gian phía client bao quanh toàn bộ lời gọi adapter, gồm chuẩn
bị ảnh, JPEG/base64, HTTP và chờ Ollama. Dòng `[ollama]` là timing do server
Ollama trả về, giúp tách thời gian load model, xử lý prompt và sinh output.

## 10. Cấu hình mặc định

| Cấu hình | Mặc định | Phạm vi |
|---|---:|---|
| `window_seconds` | `5.0` | constructor pipeline |
| `max_video_windows` | `1` | constructor pipeline |
| `motion_fps` | `5.0` | constructor pipeline |
| `yolo_fps` | `2.0` | constructor pipeline |
| `max_keyframes` | `2` | constructor pipeline |
| `YOLO_WEIGHTS` | `yolo26n.pt` | env |
| `CAMERA_AI_VLM` | `ollama` | env của API |
| `CAMERA_AI_VLM_POLICY` | `gated` | env, áp dụng cho ảnh tĩnh |
| `OLLAMA_MODEL` | `qwen3-vl:4b-instruct-q4_K_M` | env |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | env |
| `OLLAMA_NUM_CTX` | `4096` | env |
| `OLLAMA_NUM_PREDICT` | `128` | env |
| `OLLAMA_KEEP_ALIVE` | `10m` | env |
| `OLLAMA_FRAME_MODE` | `composite` | env |

Các tham số constructor video hiện chưa được expose thành env tại FastAPI.

## 11. Chạy thử

Từ thư mục dự án:

```powershell
cd D:\CongViec\CameraAI\CameraAI

$env:YOLO_WEIGHTS="yolo26n.pt"
$env:OLLAMA_MODEL="qwen3-vl:4b-instruct-q4_K_M"
$env:OLLAMA_BASE_URL="http://localhost:11434"
$env:OLLAMA_NUM_CTX="4096"
$env:OLLAMA_NUM_PREDICT="128"
$env:OLLAMA_KEEP_ALIVE="10m"
$env:OLLAMA_FRAME_MODE="composite"

python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

Mở `http://127.0.0.1:8000/` để upload ảnh/video. Không cần chạy thêm
`ollama serve` nếu Ollama desktop đã chiếm cổng `11434`.

Kiểm tra model và processor Ollama:

```powershell
ollama list
ollama ps
```

So sánh lại request hai ảnh cũ bằng cách dừng API, đổi mode rồi khởi động lại:

```powershell
$env:OLLAMA_FRAME_MODE="separate"
python -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
```

Cold-run đầu tiên thường chậm hơn do Ollama phải load model. Khi so sánh hiệu
năng, nên chạy warm-up trước rồi dùng cùng một video cho các lần đo.
## Conservative Qwen gate for async video

Each five-second async window now follows:

```text
Motion -> YOLO / optional specialized detector -> Event Router -> VLMCallPolicy
  no candidate and usable frames -> skip Qwen, emit a green window
  candidate -> call Qwen 4B once with the primary candidate
  no usable frames -> skip Qwen, emit a degraded window
```

Bounding boxes remain internal evidence for proximity and future tracking/zone
rules; raw coordinates are not added to the Qwen prompt. A fire candidate only
comes from the optional specialized detector after temporal confirmation, not
from the default COCO YOLO model.

The processing target is p95 at or below 5,000 ms per window, excluding queue
wait. This is an observed benchmark target rather than a guarantee: real API +
Ollama measurements determine whether the target is met.
