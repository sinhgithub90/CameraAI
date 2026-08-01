# Camera AI Event Analysis Architecture

## Mục tiêu

Tài liệu này chốt kiến trúc mở rộng cho CameraAI dựa trên pipeline hiện tại:

```text
Motion → YOLO26n → chọn 2 keyframe → Qwen 4B → PipelineResult
```

Mục tiêu là tăng khả năng phân tích sự kiện theo từng bước có thể đo lường,
không làm vỡ API hiện tại và không tối ưu kiến trúc trước khi có baseline.

Phạm vi gồm data contract, luồng xử lý, benchmark, Event Router, VLM routing,
model chuyên biệt, tracking tối thiểu, Zone/Line và Rule Engine. Không yêu cầu
triển khai toàn bộ các giai đoạn trong một lần.

## Trạng thái hiện tại

`SecurityAIPipeline` nằm trong `src/camera_ai/pipeline.py` và không phụ thuộc
FastAPI. Runtime hiện tại có hai đường tương thích:

- Sync vẫn dùng `analyze_event()`; video sync mặc định xử lý một cửa sổ 5 giây
  đầu (`max_video_windows=1`).
- Async video coi upload là nguồn stream theo segment: segment 0–5 giây được
  đưa vào queue ngay; segment kế tiếp chỉ được phát sau mỗi mốc 5 giây.
- Motion được lấy mẫu ở 5 FPS.
- YOLO26n chạy trên frame có motion, tối đa 2 FPS.
- Mỗi cửa sổ chọn tối đa 2 keyframe; cả window tĩnh cũng có một VLM task để
  trả kết quả theo từng segment.
- Qwen3-VL 4B chạy qua Ollama một lần cho mỗi window async.
- `OLLAMA_FRAME_MODE=composite` ghép đúng 2 frame thành một ảnh.
- Ảnh tĩnh dùng `VLMGate`; video async không gate VLM theo motion.
- `VideoFrameObservation`, `MotionResult`, `VideoWindowResult` và timing đã có.
- `CandidateEvent`, `ModelDecision`, `AlertEvent` và tracking chưa có trong
  pipeline runtime.
- `FireDetector` tồn tại nhưng không được khởi tạo bởi pipeline mặc định.
- Nhánh async có `VideoAnalysis`/`AnalysisStore`, `AlertStore`, EventBus và
  một `VLMQueue`/`VLMWorker`. Với task video, worker này thực hiện trọn
  Motion → YOLO → keyframe → VLM; với ảnh tĩnh, nó chỉ chạy VLM.
- `GET /analyses/{analysis_id}` là nguồn dữ liệu của FE video: trả status,
  timing cộng dồn và `VideoWindowResult` theo thứ tự thời gian. FE không vẽ
  bounding box/detection table cho video.
- Prompt Qwen yêu cầu JSON có `summary`, `alert_level`, `risks` và
  `recommended_action`; response summary rỗng bị đánh dấu degraded và có
  fallback hiển thị ở backend lẫn FE.
- `src/camera_ai/events.py` hiện dành cho EventBus; domain event contracts không
  được đặt vào file này.

Chi tiết runtime nằm trong
[`camera-ai-pipeline.md`](../../camera-ai-pipeline.md).

## Nguyên tắc thiết kế

1. **Đo trước, mở rộng sau.** Mọi thay đổi ảnh hưởng latency hoặc độ chính xác
   phải được so sánh với baseline bằng cùng bộ video.
2. **Observation không phải kết luận.** Dữ liệu detector và motion không được
   đặt tên như một cảnh báo đã xác nhận.
3. **Candidate là nghi vấn.** Router chỉ tạo giả thuyết để chọn model hoặc
   prompt; không tự kết luận tai nạn, đánh nhau hay té ngã.
4. **VLM là lớp xác minh cho sự kiện khó.** Luật chắc chắn như line crossing
   hoặc dwell time không cần gọi VLM nếu đã đạt điều kiện nghiệp vụ.
5. **Tương thích ngược.** `PipelineResult`, fallback Ollama và FastAPI adapter
   tiếp tục hoạt động trong các giai đoạn đầu.
6. **Tracking độc lập với Zone/Line.** ByteTrack được thử nghiệm như một lớp
   bổ sung trước khi xây luật không gian.
7. **Không dùng confidence tự sinh của VLM làm ngưỡng cứng ban đầu.** Giá trị
   này được lưu để đánh giá; escalation ban đầu dựa trên `uncertain`, mâu thuẫn
   tín hiệu và mức nghiêm trọng.

## Kiến trúc logic đích

```text
Camera / upload
      ↓
Decode + sampled frame buffer
      ↓
MotionDetector
      ↓
YOLO26n / specialized detectors
      ↓
VideoWindowObservation
      ↓
Event Router
      ↓
CandidateEvent (zero or more)
      ↓
ModelDecision
  ├── deterministic rule result
  ├── specialized detector result
  ├── VLM Fast 2B, if benchmark justifies it
  └── VLM Strong 4B, only for escalation
      ↓
AlertEvent (zero or more)
      ↓
PipelineResult compatibility projection
```

Tracking là nhánh tùy chọn sau YOLO:

```text
YOLO detections → ByteTrack → track history
                                  ↓
                         Candidate / Zone / Line rules
```

Nhánh thực thi async hiện tại cho video dùng một queue cấp window:

```text
stream segment 0–5s
      ↓
VideoAnalysis window (pending)
      ↓
VLMQueue → VLMWorker
      ↓
Motion → YOLO → keyframe → Qwen
      ↓
AnalysisStore.complete_processed_window()
      ↓
GET /analyses/{analysis_id} → FE card per window
```

Ảnh async giữ đường VLM-only cũ. Đây là execution mechanism, không thay thế
domain contract. EventBus vẫn là
transport cho các event hệ thống như `alert.created` và `alert.vlm_confirmed`.

### Ranh giới cần giữ khi nâng cấp

`VLMQueue` là tên lịch sử: về nghĩa thực tế nó đang là **single window
pipeline queue** cho video. Khi tách stage sau này, giữ nguyên
`analysis_id`, `window_index`, `alert_id` và `VideoWindowResult`; có thể thay
bằng `WindowQueue → MotionQueue → YOLOQueue → VLMQueue` mà không đổi API
`/analyses` hay FE. Không tách queue trước khi benchmark chứng minh cần
throughput cao hơn một worker.

## Data contract

### VideoWindowObservation

Lớp này mô tả bằng chứng quan sát được trong một cửa sổ. Nó không chứa các từ
ngữ kết luận như `accident`, `fighting`, `intrusion` hoặc `danger`.

Các trường mục tiêu:

```json
{
  "camera_id": "cam_01",
  "window_id": "cam_01_000124",
  "start_ms": 620000,
  "end_ms": 625000,
  "motion": {
    "max_score": 0.72,
    "mean_score": 0.34,
    "active_regions": []
  },
  "detections": [],
  "selected_frames": [12, 20]
}
```

Trong code hiện tại, `VideoFrameObservation` và `MotionResult` là building
block đã có. Giai đoạn chuẩn hóa sẽ thêm model cấp cửa sổ và serializer thay vì
đổi ý nghĩa của hai model hiện tại.

### CandidateEvent

Candidate là giả thuyết có bằng chứng và mục đích routing:

```json
{
  "candidate_id": "candidate_001",
  "window_id": "cam_01_000124",
  "candidate_type": "possible_person_vehicle_interaction",
  "priority": "medium",
  "evidence": {
    "has_person": true,
    "has_vehicle": true,
    "motion_peak": 0.72,
    "spatial_proximity": true
  },
  "requires_verification": true
}
```

Candidate type dùng ngôn ngữ nghi vấn, ví dụ:

- `person_only_activity`
- `vehicle_only_activity`
- `possible_person_vehicle_interaction`
- `multi_person_high_motion`
- `unknown_motion`
- `possible_fire_visual_change`
- `camera_tamper`

### ModelDecision

ModelDecision ghi rõ model nào đã đưa ra kết quả và không che giấu việc model
không chắc chắn:

```json
{
  "candidate_id": "candidate_001",
  "model": "qwen3-vl:4b-instruct-q4_K_M",
  "decision": "uncertain",
  "event_type": "possible_traffic_incident",
  "evidence": ["Các frame chưa cho thấy rõ va chạm"],
  "raw_output_valid": true,
  "latency_ms": 4183.7
}
```

`decision` có ba giá trị: `yes`, `no`, `uncertain`. Confidence của VLM nếu có
được lưu để phân tích, nhưng chưa dùng làm policy threshold ở phiên bản đầu.

### AlertEvent

Alert chỉ được phát hành sau khi policy xác nhận candidate:

```json
{
  "alert_id": "alert_001",
  "camera_id": "cam_01",
  "event_type": "traffic_incident",
  "severity": "high",
  "status": "pending_review",
  "source_candidate_id": "candidate_001"
}
```

Các cảnh báo cũ trong `PipelineResult.security` tiếp tục được tạo bằng cách
chiếu AlertEvent/ModelDecision tốt nhất về `alert_level`, `risks` và
`recommended_action`.

### Mapping sang async AlertStore

`AlertEvent` là domain-level event, còn `alert_store.Alert` là persistence
model của execution async hiện tại. Chúng được nối qua mapping rõ ràng:

```text
CandidateEvent
    ↓ policy creates
AlertEvent
    ↓ persistence adapter
alert_store.Alert
    ↓ VLM worker update
AlertStore.Alert.vlm + AlertStore.Alert.security
```

`AlertStore.Alert.id` giữ `alert_id`; `rule_id` giữ nguồn candidate/rule; trạng
thái VLM dùng `VLMResult.status` (`pending`, `completed`, `skipped`). Không đổi
ý nghĩa của `events.Event`, vì đó là message của EventBus.

## Luồng và policy

### Baseline

Giữ nguyên các mặc định hiện tại:

```text
window_seconds = 5.0
motion_fps = 5.0
yolo_fps = 2.0
max_keyframes = 2
OLLAMA_FRAME_MODE = composite
```

Không tăng lên 4–8 frame trước khi có benchmark. Các cấu hình cần so sánh sau
này là: 2 composite, 2 separate, 4 separate và 6 separate.

### Event Router

Router ban đầu chỉ dùng motion, YOLO class, số detection, bbox và thay đổi
tương đối giữa keyframe. Router có thể tạo nhiều candidate cho một cửa sổ,
nhưng không kết luận sự kiện cuối cùng.

### VLM routing

Fast 2B chỉ được đưa vào production sau benchmark offline với cùng candidate và
cùng prompt family như Strong 4B. Nếu cascade được chấp thuận, policy ban đầu là:

```text
Fast = uncertain              → Strong
Fast mâu thuẫn rule/model     → Strong
Candidate critical            → Strong hoặc double-check
Fast sai JSON schema          → Strong
Fast = yes/no hợp lệ          → chấp nhận theo policy sự kiện
```

Nếu chi phí đổi model trong Ollama làm mất lợi ích latency, hệ thống giữ một
model 4B duy nhất.

### Specialized detector

Fire/Smoke là ứng viên đầu tiên theo mặc định, nhưng phải qua temporal validation:

```text
detector signal
→ xác nhận liên tiếp 3–5 frame
→ CandidateEvent suspected_fire
→ VLM xác minh nếu confidence/chất lượng bằng chứng chưa đủ
```

Heuristic màu cam/vàng không được xem là kết luận cháy. Bộ test phải có hard
negative như đèn, nắng, biển quảng cáo, áo cam, hàn kim loại, hơi nước và sương.

### Tracking, Zone/Line và Rule Engine

ByteTrack chỉ lưu `track_id`, class, thời gian tồn tại, bbox history và center
history trong giai đoạn đầu. Sau khi track ID đủ ổn định mới thêm:

1. enters/exits zone;
2. line crossing;
3. dwell time;
4. đếm xe;
5. dừng lâu, đi sai hướng, ùn tắc;
6. suspected collision rồi chuyển sang VLM xác minh.

## Benchmark và observability

Bộ benchmark phải có video bình thường và bất thường:

- giao thông bình thường;
- người đi bộ và xe bình thường;
- tai nạn;
- người ngã;
- tụ tập không đánh nhau;
- đánh nhau;
- cháy/khói;
- ánh sáng màu cam không cháy;
- camera rung hoặc bị che.

Mỗi run cần lưu được:

```text
observation.json
candidate.json
selected_frame_*.jpg
vlm_prompt.txt
vlm_raw_output.txt
decision.json
alert.json
```

Các chỉ số bắt buộc:

- Motion, YOLO, keyframe selection, Ollama và tổng latency;
- số lần gọi detector/VLM;
- tỷ lệ JSON hợp lệ;
- recall theo event type;
- false positive và false negative;
- RAM/VRAM và chi phí chuyển model cho benchmark 2B/4B;
- payload size và chất lượng theo cấu hình keyframe.

## Xử lý lỗi và tương thích

- Video không mở được hoặc không có frame tiếp tục trả `ValueError` hiện tại.
- Lỗi detector ở một frame được log và không chặn VLM nếu vẫn có keyframe.
- Ollama lỗi tiếp tục trả `degraded=true` theo cơ chế hiện tại.
- Output VLM sai schema tạo `ModelDecision` không hợp lệ và được escalation hoặc
  fallback, không tự phát hành alert high.
- `PipelineResult` và FastAPI adapter được giữ tương thích cho đến khi có API
  version mới được thống nhất.

## Tiêu chí hoàn thành kiến trúc

Kiến trúc sẵn sàng cho implementation khi:

- baseline hiện tại đo được trên bộ video cố định;
- bốn data layer có schema và owner rõ ràng;
- Router không kết luận thay VLM/rule policy;
- policy cascade có tiêu chí benchmark cụ thể;
- specialized detector có temporal validation;
- tracking được tách độc lập khỏi Zone/Line;
- mọi stage có log và test có thể tái lập;
- kế hoạch triển khai có thể thực hiện từng task mà vẫn giữ pipeline chạy được.
