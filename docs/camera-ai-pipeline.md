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
Video upload/stream
  |
  |-- đọc stream và đóng ranh giới theo source time mỗi 5 giây
  |
  |-- pre-queue admission theo (analysis_id, camera_id)
  |     |-- cooldown còn hiệu lực -> bỏ cửa sổ, chỉ cộng cooldown summary
  |     |-- đến hạn recheck -> reserve nguyên tử đúng 1 cửa sổ
  |     `-- trạng thái bình thường -> admit
  |
  |-- global priority queue
  |
  |-- Motion 5 FPS -> YOLO tối đa 2 FPS -> Event Router
  |
  |-- chọn tối đa 2 keyframe -> ghép TRUOC/SAU
  |
  `-- Qwen -> severity -> episode/cooldown state -> compact result
```

Producer vẫn đọc nguồn và tạo ranh giới 5 giây khi camera đang cooldown để
stream không bị trễ. Tuy nhiên cửa sổ bị suppress không tạo `VLMTask`, alert
tương thích hay `VideoWindowResult`; vì vậy nó không chạy Motion, YOLO, router,
keyframe hoặc Qwen và không chiếm queue.

`SecurityAIPipeline` được khởi tạo trực tiếp vẫn có mặc định phát triển
`max_video_windows=1`. FastAPI chủ động dùng `max_video_windows=None`, nên
`POST /async/analyze/video` đọc toàn bộ video thay vì chỉ 5 giây đầu. API không
nhận phân tích thứ hai đang hoạt động cho cùng `camera_id` và trả HTTP 409.

Với đường synchronous/direct-construction, `frames_read` vẫn đếm mọi frame đã
`grab`, nhưng ảnh BGR chỉ được `retrieve` theo nhịp Motion. Ví dụ 5 giây video
30 FPS có 150 lần `grab` và khoảng 25 lần `retrieve` ở Motion 5 FPS.

## 3. Điều kiện gọi YOLO và Qwen trong video

| Trạng thái ranh giới 5 giây | Vào queue | Motion/YOLO | Qwen |
|---|---:|---:|---:|
| Đang red/orange cooldown, chưa đến hạn | Không | Không | Không |
| Recheck đến hạn | Có, đúng 1 reservation | Có | Bắt buộc nếu có frame dùng được |
| Đã admit nhưng cửa sổ tĩnh | Có | Motion có, YOLO không | Không |
| Router không tạo candidate | Có | Có theo motion | Không |
| Router có candidate | Có | Có theo motion | Gọi 1 lần |
| Recheck không có frame dùng được | Có | Có thể dừng sớm | Không; đánh dấu failed và retry |

Luồng async dùng `Event Router` và `VLMCallPolicy`: candidate mới quyết định có
gọi Qwen hay không. Motion không detection vẫn có thể tạo
`unexplained_motion`, nhờ đó Qwen có thể đánh giá khói, vật cản hoặc vật thể nằm
ngoài nhãn COCO. `VLMGate` chỉ là policy của ảnh tĩnh.

### 3.1 Cooldown và xử lý đồng thời

Khi Qwen xác nhận red, runtime đặt `next_recheck_event_seconds` bằng cuối cửa
sổ cộng 60 giây. Producer kiểm tra state trước persistence/queue. Đến hạn,
`recheck_reserved` cùng `state_version` bảo đảm chỉ một cửa sổ được enqueue;
completion hoặc failure đều giải phóng đúng reservation token. Red lặp lại gia
hạn 60 giây, medium chuyển sang orange watch 15 giây, low resolve episode, còn
failure giữ cảnh báo và dùng retry backoff 15, 15, 30 rồi tối đa 60 giây.

Ngay khi red được tạo hoặc gia hạn, worker prune các task đang chờ có cùng
`(analysis_id, camera_id)` và source start trước deadline mới. Queue đồng thời
lưu cutoff để từ chối task đã được admit ngay trước red nhưng enqueue đến sau
lúc prune. Camera/analysis khác không bị ảnh hưởng. `VideoWindowProcessor` vẫn
kiểm tra cooldown ở đầu hàm như lớp bảo vệ dự phòng cho task cũ hoặc adapter
không dùng producer admission; đây không phải đường suppress chính.

Video upload so sánh deadline bằng source seconds. Adapter live-camera phải
dùng `time.monotonic()` cho quyết định cooldown; UTC chỉ nên dùng để hiển thị.

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
- prompt hình ảnh đã được router chọn theo profile; candidate, router evidence
  và tóm tắt YOLO không được đưa vào nội dung prompt;
- yêu cầu trả cảnh báo an ninh ngắn bằng tiếng Việt;
- `temperature=0`, context 4096 và tối đa 128 output token.

JSON Schema của Qwen yêu cầu đúng ba trường:

```json
{
  "decision": "yes | no | uncertain",
  "event_type": "traffic_accident",
  "summary": "Mô tả ngắn bằng tiếng Việt"
}
```

`event_type` đã validate quyết định severity cuối cùng; router priority không
được nâng hoặc hạ cảnh báo. `no` chỉ hợp lệ với `no_event`, `uncertain` với
`unknown_event`, còn `yes` phải đi cùng event cụ thể.

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

Trong async API, chỉ cửa sổ đã được admission và thực sự đi vào processing mới
tạo `VideoWindowResult`. Cửa sổ bị producer drop hoặc worker prune không tạo
record giả trong `windows`; chúng được cộng dồn vào top-level `cooldown`.

Một cửa sổ processed gồm:

- khoảng thời gian cửa sổ;
- toàn bộ detection thu được trong cửa sổ;
- kết quả VLM và quyết định an ninh;
- số keyframe và các frame/timestamp đã gửi Qwen;
- timing Motion, detector và Qwen.

Response compact của `GET /analyses/{analysis_id}` bỏ detection boxes,
candidate, raw alert, trace và `event_metadata`; các dữ liệu này vẫn có thể tồn
tại model nội bộ. Trạng thái cooldown hiện tại được trả một lần:

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

`suppressed_windows` gồm cả ranh giới producer đã drop và pending task worker
đã prune. `suppressed_seconds` là tổng source duration tương ứng. Khi episode
chuyển high sang medium, `alert_level` và `recheck_at` được cập nhật; episode
red mới có `red_started` mới.

Đường synchronous `PipelineResult` vẫn tổng hợp cửa sổ có mức cảnh báo cao
nhất làm summary/security và dùng frame đại diện của cửa sổ đó.

Nếu không cửa sổ nào có motion, pipeline trả kết quả skipped mức `low`, không
gọi YOLO/Qwen, nhưng vẫn trả ảnh đại diện cuối cùng đã decode.

## 9. Output và timing

Các trường chính của synchronous `PipelineResult`:

- `request_id`, `media_type`, `camera_id`;
- `detections`;
- `vlm.summary`, `vlm.observations`, `vlm.degraded`, `vlm.skipped`;
- `security.alert_level`, `security.risks`, `security.recommended_action`;
- `annotated_image` là JPEG base64;
- `video_stats` cho video;
- `video_windows` cho kết quả synchronous/direct-construction.

Async API trả `CompactVideoAnalysis` gồm `id`, `camera_id`, `status`,
`total_timing`, danh sách processed `windows` và optional top-level `cooldown`.
Mỗi compact window chỉ giữ range, resolved alert level, Qwen status/summary,
optional episode transition và timing.

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

Timing async cần đọc riêng từng lớp:

- `total_ms`: Motion + detector + keyframe + Qwen của cửa sổ đã chạy;
- `queue_wait_ms`: thời gian task chờ trước khi worker bắt đầu;
- `wall_clock_ms`: từ lúc enqueue đến khi processing hoàn tất.

Budget 5.000 ms và `processing_p95_ms` dùng `total_ms`, không gồm queue wait.
Call rate dùng số cửa sổ logic
`len(windows) + cooldown.suppressed_windows`; cửa sổ suppress không được thêm
giá trị timing 0 giả vào p95.

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

Only a window accepted by pre-queue admission reaches this processing policy:

```text
5-second boundary -> pre-queue admission -> global queue
  admitted -> Motion -> YOLO / optional detector -> Event Router -> VLMCallPolicy
  no candidate and usable frames -> skip Qwen, emit a green window
  candidate -> call Qwen 4B once with the primary candidate
  no usable frames -> skip Qwen, emit a degraded window
  active cooldown -> no queue task and no per-window result
```

Bounding boxes remain internal evidence for proximity and future tracking/zone
rules; raw coordinates are not added to the Qwen prompt. A fire candidate only
comes from the optional specialized detector after temporal confirmation, not
from the default COCO YOLO model.

Router candidates describe scene composition and only control whether Qwen is
called. They are `person_vehicle_scene`, `multi_person_scene`, `person_scene`,
`vehicle_scene`, `unexplained_motion`, and the specialized
`temporally_confirmed_fire_signal`. They are not final event classifications.
`vehicle_scene` and `person_vehicle_scene` select the traffic visual prompt;
other candidates select the generic visual prompt. Candidate values, router
evidence, and YOLO summaries stay internal. Traffic prompts classify temporal
vehicle contact and abnormal relative positions directly from the images.

With the default two-keyframe configuration, the selector smooths change scores
across three observations, expands an activity span at 30% of the smoothed peak
while tolerating one inactive sample, then selects context 0.6 seconds before
the span and 0.6 seconds after it. If no change exists it uses the first and
last observations. Selection stays inside the current five-second window and
requires no extra detector or model call.

The processing target is p95 at or below 5,000 ms per window, excluding queue
wait. This is an observed benchmark target rather than a guarantee: real API +
Ollama measurements determine whether the target is met.

For a routed candidate, Qwen returns only `decision`, `event_type`, and a short
`summary`. Event type must be one of `no_event`, `person_vehicle_interaction`,
`traffic_accident`, `person_fall`, `fighting`, `fire_smoke`, `camera_tamper`, or
`unknown_event`. Valid `no` pairs with `no_event`; valid `uncertain` pairs with
`unknown_event`; valid `yes` pairs with a concrete event. Invalid or inconsistent
JSON becomes low/degraded `uncertain + unknown_event` and cannot create an alert.

For a valid affirmative decision, `event_type` alone determines UI severity:

- green/low: `no_event`, `person_vehicle_interaction`, `unknown_event`;
- orange/medium: `person_fall`, `camera_tamper`;
- red/high: `traffic_accident`, `fighting`, `fire_smoke`.

Router candidate priority remains useful for queue ordering and primary
candidate selection, but cannot raise or lower the final alert severity.
