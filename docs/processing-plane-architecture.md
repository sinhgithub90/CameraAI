# Processing Plane — Kiến Trúc Pipeline & Thuật Toán

> **Phạm vi**: Thiết kế chi tiết tầng xử lý lõi (Processing Plane) cho Camera AI Platform.
> **Ngày**: 2026-08-01.

Tài liệu này đi sâu vào **cách pipeline xử lý frame từ N camera**, bao gồm kiến
trúc các tầng, thuật toán chọn frame, chiến lược gom sự kiện, và cơ chế chống
bỏ sót.

---

## 1. Tổng Quan Pipeline

```
N Camera ──┐
           │   ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
Camera₁ ───┼──▶│ Ingestion│───▶│ Detection│───▶│   Rule   │───▶│   VLM    │
Camera₂ ───┤   │ Gateway  │    │ Engine   │    │ Engine   │    │  Worker  │
  ...      │   │          │    │ (YOLO)   │    │          │    │ (Qwen)   │
Cameraₙ ───┘   └────┬─────┘    └────┬─────┘    └────┬─────┘    └────┬─────┘
                    │               │               │               │
               Frame Buffer    Detection[]      Alert +            │
               (ring, RAM)     + frame meta     VLM Task      Analysis
                    │               │               │               │
                    └───────────────┴───────────────┴───────────────┘
                                    │
                              EVENT BUS
                                    │
                         ┌──────────┴──────────┐
                         ▼                     ▼
                   Alert Service         Frame Storage
```

Pipeline xử lý theo mô hình **staged event-driven**: mỗi tầng nhận input, xử lý,
publish output lên Event Bus. Tầng sau subscribe event của tầng trước. Các tầng
hoạt động **bất đồng bộ và độc lập** — không tầng nào block tầng nào.

---

## 2. Ingestion Gateway — Nhận & Lọc Frame

### 2.1 Bài toán

N camera IP, mỗi camera 25-30 FPS. Nếu xử lý hết: N×30 = hàng trăm frame/giây →
GPU không kham nổi. Nhưng an ninh không cần 30 FPS — chuyển động quan trọng diễn
ra trong vài giây, không phải vài millisecond.

### 2.2 Kiến trúc

```
Với MỖI camera:

Camera Source ──▶ Decoder ──▶ Frame Ring ──▶ Motion Detector ──▶ Downstream
 (RTSP/HTTP)      (FFmpeg)    (N giây)      (frame diff)        (Event Bus)
```

**Camera Source**: mỗi camera là 1 async task độc lập. Mở RTSP stream (hoặc nhận
HTTP push), decode frame. Khi mất kết nối → auto-reconnect với exponential
backoff. Camera offline → publish `camera.offline` event.

**Frame Ring**: ring buffer cố định (vd 300 frame = 10 giây @30fps hoặc 60 giây
@5fps) lưu frame gốc full resolution. Dùng cho 2 mục đích: (a) motion detection
so sánh frame hiện tại với frame trước, (b) VLM sau này cần frame context xung
quanh sự kiện.

**Motion Detector**: so sánh frame hiện tại với frame trước đó 0.5-1 giây. Tính
% pixel thay đổi. Nếu dưới ngưỡng → frame tĩnh → **skip, không đẩy xuống
Detection**. Đây là tầng lọc rẻ nhất (CPU, không GPU) giúp loại bỏ 70-95% frame
với camera tĩnh.

### 2.3 Adaptive Sampling

Không phải mọi camera đều cần sample như nhau:

```
priority=5 (cổng chính, kho tiền):  5 FPS sample, motion gate thấp
priority=3 (hành lang, bãi xe):     2 FPS sample, motion gate bình thường  
priority=1 (camera phụ, ít dùng):   1 FPS sample, motion gate cao
```

Ngoài ra, sample rate thích nghi theo thời gian:
- Giờ cao điểm (8h-18h): sample FPS cao
- Đêm (22h-6h): sample FPS thấp, motion gate cao hơn (frame tĩnh đêm dễ skip)
- **Event boost**: khi có alert đang active → tạm tăng sample FPS của camera đó
  lên max trong 30 giây để bắt thêm chi tiết cho VLM

### 2.4 Thuật toán: Motion Detection

```python
def motion_score(prev_frame, curr_frame, threshold=0.5):
    # Resize cả 2 về 320x240 để tính nhanh trên CPU
    prev = resize(prev_frame, 320, 240)
    curr = resize(curr_frame, 320, 240)
    diff = abs(curr - prev)
    motion_pct = count_nonzero(diff > 25) / total_pixels
    return motion_pct > threshold  # threshold = 0.5% pixels thay đổi
```

Chi phí: < 1ms/frame trên CPU. So với YOLO 15ms trên GPU → tiết kiệm 15× khi
frame tĩnh.

---

## 3. Detection Engine — YOLO Trên GPU

### 3.1 Bài toán

Frame từ nhiều camera đổ về liên tục. Nếu gọi YOLO tuần tự từng frame → GPU
under-utilized (mỗi lần gọi 15ms, GPU chờ CPU chuẩn bị frame tiếp). Batch
inference tận dụng GPU parallelism.

### 3.2 Batch Accumulator

```
Frame từ Camera₁ ──┐
Frame từ Camera₂ ──┼──▶ [Batch Queue] ──▶ YOLO GPU ──▶ Detection[]
Frame từ Camera₃ ──┘    (max 8 frame
                          hoặc 50ms timeout)
```

Thuật toán:
1. Frame đến → đưa vào batch queue
2. Khi queue đạt **batch_size=8** HOẶC **timeout 50ms** (whichever first) → flush batch qua YOLO
3. Kết quả detection được demux về từng camera dựa trên camera_id đi kèm frame

**Timeout 50ms** đảm bảo: nếu chỉ có 1 camera hoạt động, frame không phải chờ
lâu để đủ batch. **Batch size 8** đảm bảo GPU utilization cao với nhiều camera.

**Resize về chung resolution** (640px) trước khi batch — YOLO yêu cầu tất cả
frame trong batch cùng kích thước.

### 3.3 Object Detection + Classification

```
YOLO26n output:
  person (0.92), car (0.85), truck (0.71), ...
  
Mỗi detection:
  { label, confidence, bbox[x1,y1,x2,y2], camera_id, frame_timestamp }
```

Detection được publish lên Event Bus dưới dạng `detection.completed` event.
**Đây là điểm output đầu tiên** — từ lúc frame vào đến detection ra < 100ms.

### 3.4 Multi-Camera Scheduling

Khi tổng FPS từ các camera vượt khả năng GPU:

```
GPU capacity = 1000ms / (15ms / batch_8) × 8 = ~530 detections/giây

Với 50 camera × 5 FPS = 250 fps → ok (250 < 530)
Với 100 camera × 5 FPS = 500 fps → sát giới hạn
Với 200 camera × 5 FPS = 1000 fps → vượt, cần drop
```

**Thuật toán weighted round-robin**:
- Camera priority 5: weight 5
- Camera priority 3: weight 3
- Camera priority 1: weight 1

Mỗi vòng lặp, chọn camera theo weight. Camera có priority cao được sample nhiều
hơn. Khi quá tải, drop frame từ camera priority thấp trước.

**Đảm bảo không miss**: dù có drop frame, camera vẫn được sample ít nhất 1 FPS
ngay cả khi priority=1. Không camera nào bị "đói" hoàn toàn.

### 3.5 Detector Đặc Thù (Fire/Smoke)

Chạy song song với YOLO COCO trên cùng frame. Fire detector là model YOLO nhẹ
(~5MB) hoặc heuristic màu (HSV range). Output: `fire (0.7)`, `smoke (0.6)`.

Fire detection được publish với `priority=HIGH` lên Event Bus, Rule Engine xử lý
ngay — không cần chờ.

---

## 4. Rule Engine — Từ Detection → Quyết Định

### 4.1 Bài toán

Detection đơn thuần ("có person") không phải là sự kiện an ninh. Cần **context**
để quyết định: person ở đâu? Lúc nào? Bao lâu? Có phải xâm nhập không?

### 4.2 Kiến trúc

```
detection.completed event ──▶ [Rule Matcher] ──▶ Action
                                    │
                                    ├─ Match: duyệt rule của tenant
                                    ├─ Context: zone, time, persistence
                                    ├─ Dedup: trùng với alert đang active?
                                    └─ Execute: alert ngay +/hoặc queue VLM
```

### 4.3 Thuật toán: Rule Matching

Với mỗi `detection.completed` event:

```
1. Lấy tenant_id từ camera → load rules của tenant đó
2. Lọc rules theo camera.enabled_rule_ids
3. Với mỗi rule:
   a. Check trigger.detections: detection labels có match không?
   b. Check trigger.zone: camera.zone có trong rule zone list không?
   c. Check trigger.schedule: thời gian hiện tại có trong khoảng không?
   d. Nếu match → tăng persistence counter cho rule+camera này
4. Rules có persistence đạt ngưỡng → execute action
```

### 4.4 Thuật toán: Persistence & Dedup

**Persistence**: 1 frame có person không đủ — cần N frame trong T giây.

```
State lưu trong memory (per camera, per rule):
  { camera_id, rule_id, first_seen_ts, last_seen_ts, count }

Mỗi lần rule match:
  count += 1
  last_seen_ts = now
  
Khi count >= rule.trigger.persistence
  VÀ last_seen_ts - first_seen_ts <= rule.trigger.time_window
  → KÍCH HOẠT ALERT
  → Reset state

Nếu last_seen_ts - first_seen_ts > time_window mà chưa đủ persistence:
  → Reset state (sự kiện quá ngắn, không đủ persistence)
```

**Dedup**: cùng camera + rule, trong cooldown window:

```
Khi rule kích hoạt:
  Check: có alert nào status=active, camera_id=X, rule_id=Y không?
  Có → update alert hiện tại (thêm detection, refresh timestamp)
       KHÔNG tạo alert mới
  Không → tạo alert mới
  
Alert hết hạn sau: last_seen_ts + cooldown
  Nếu không có detection mới trong cooldown → tự động resolve
```

### 4.5 Quyết Định: Alert Ngay Hay Chờ VLM?

```
rule.action.alert_immediately:
  true  → Alert ngay với initial_level
          Nếu rule có vlm.enabled → queue VLM task (enrich)
          VLM xong → update alert, có thể nâng/hạ level
  
  false → Queue VLM task TRƯỚC
          VLM xong → tạo alert với confirmed_level từ VLM
          Nếu queue quá tải + escalate_after hết hạn → alert với initial_level
```

Đây là cơ chế chống miss cốt lõi: **alert ngay không chờ VLM** cho fire/smoke;
**alert sau VLM** cho intrusion (giảm false positive); và **escalation timer**
đảm bảo nếu VLM chậm thì alert vẫn được gửi.

---

## 5. VLM Worker — Phân Tích Chuyên Sâu

### 5.1 Bài toán

VLM là tài nguyên khan hiếm nhất: 1 GPU = 1 inference/lúc, mỗi inference 3-8
giây. Với nhiều camera, queue có thể dài → cần priority, cần chọn frame thông
minh để 1 lần gọi VLM cho kết quả chính xác nhất.

### 5.2 Priority Queue

```
Queue ──▶ [priority=1] fire/smoke ──────────────▶ VLM Worker
         [priority=2] intrusion, fall ──────────▶
         [priority=3] crowd, loitering ─────────▶
         [priority=4] vehicle, object ──────────▶
```

**Thuật toán priority động**:

```
base_priority = rule.action.vlm.priority   # 1-4
wait_time = now - enqueued_at

if wait_time > 30s:  effective_priority = max(1, base_priority - 1)
if wait_time > 60s:  effective_priority = max(1, base_priority - 2)
if wait_time > 120s: effective_priority = 1  # khẩn cấp — xử lý ngay

Dequeue: lấy item có effective_priority nhỏ nhất, nếu bằng nhau thì FIFO
```

Đảm bảo: (a) fire luôn được xử lý trước, (b) routine task không bị starvation
— sau 2 phút, mọi task đều thành priority 1.

### 5.3 Frame Selection Algorithms

Đây là **thuật toán quan trọng nhất** của pipeline — quyết định VLM có thấy
đúng sự kiện hay không. Input: camera_id + time window [t_start, t_end]. Output:
danh sách frame (keyframes) gửi cho Qwen.

#### Strategy 1: Event-Aware (dùng cho video đã quay / window)

```
Input: camera_id, [t_start, t_end], detection results trong window
Output: tối đa K keyframe

Bước 1 — Gom detection thành cụm thời gian:
  Duyệt frame đã sample theo thứ tự thời gian
  Frame có detection → gán vào cụm hiện tại nếu cách frame trước ≤ 2s
  Frame cách > 2s → tạo cụm mới
  
Bước 2 — Phân bổ keyframe:
  Mỗi cụm được 1-2 keyframe (tùy số lượng cụm tổng)
  Trong mỗi cụm: chọn frame có score cao nhất
    score = Σ (detection.confidence × label_weight)
    label_weight: fire=10, smoke=8, person=5, vehicle=3, other=1
  
  Frame còn lại (sau khi phân bổ cho cụm) → spread đều khắp window
  để VLM có bối cảnh tổng thể

Bước 3 — Đảm bảo độ phủ:
  Nếu có fire/smoke detection ở frame nào → frame đó LUÔN có trong keyframe
  (override mọi logic phân bổ)
```

```
Ví dụ: Window 30s, 60 frame sample, K=8
  Cụm 1 (t=5-12s): fire+smoke, 8 frame → 2 keyframe (fire luôn có mặt)
  Cụm 2 (t=20-25s): person×3, 5 frame → 2 keyframe
  Còn 4 keyframe → spread đều t=0, 10, 18, 28
  → Qwen thấy: fire ở giây 5-12 VÀ person ở giây 20-25 VÀ bối cảnh
```

#### Strategy 2: Real-Time Streaming (cho camera trực tiếp)

```
Input: camera_id, event_start_time
Output: keyframe được chọn real-time khi frame đến

Khi alert được tạo (t=0):
  Frame t=0 (frame gây trigger) → keyframe #1
  Đánh dấu: cần thêm K-1 frame context

Các frame tiếp theo (t=1s, 2s, ...):
  Nếu có detection mới → keyframe tiếp theo (tối đa 3 frame có detection)
  Nếu không có detection → lấy mỗi 2s 1 frame cho context
  
Khi đủ K frame hoặc hết window → flush batch, gọi VLM
```

#### Strategy 3: Cluster-Only (nhiều sự kiện rời rạc)

Áp dụng khi 1 window dài (60s+) có nhiều sự kiện không liên quan:

```
Phân cụm detection thành N cụm độc lập
Với mỗi cụm:
  Gửi 2-3 frame (cụm đó) + 1-2 frame context (xung quanh cụm)
  → 1 VLM call riêng cho cụm đó
  (Ưu điểm: VLM không bị nhiễu bởi sự kiện khác)
```

### 5.4 VLM Prompt Engineering Per Rule

Mỗi rule có prompt template riêng để VLM tập trung vào đúng nghiệp vụ:

```
Rule: intrusion_detection
Prompt focus: "Có dấu hiệu xâm nhập trái phép không? Người có đang cố 
  gắng vượt qua hàng rào/cửa không? Trang phục, hành vi có đáng ngờ không?"

Rule: crowd_gathering  
Prompt focus: "Có bao nhiêu người? Họ đang tụ tập hay chỉ đi ngang qua?
  Có dấu hiệu ẩu đả, hỗn loạn không?"

Rule: fire_detection
Prompt focus: "Xác nhận có lửa/khói không? Mức độ? Vị trí? Hướng lan?
  Có người trong khu vực nguy hiểm không?"
```

### 5.5 VLM Result Processing

VLM trả về JSON → map vào `SceneAnalysis`:
- `alert_level` từ VLM có thể **nâng** (medium→high) hoặc **giữ nguyên**, nhưng
  **không bao giờ hạ** nếu initial alert đã là HIGH (nguyên tắc "không miss")
- Exception: operator có thể resolve alert thành `false_alarm` thủ công
- `observations` và `risks` được lưu vào alert để dashboard hiển thị

---

## 6. Event Bus — Hệ Thần Kinh

### 6.1 Thiết kế

```
Publisher                    Event                       Subscriber
─────────                    ─────                       ──────────
Ingestion Gateway ──────▶ frame.captured ────────────▶ Detection Engine
Detection Engine ───────▶ detection.completed ───────▶ Rule Engine
                                                        Frame Storage
Rule Engine ────────────▶ alert.created ─────────────▶ Alert Service
                          vlm.task.queued ───────────▶ VLM Worker
VLM Worker ─────────────▶ alert.vlm_confirmed ───────▶ Alert Service
Ingestion Gateway ──────▶ camera.offline/online ─────▶ Health Monitor
```

### 6.2 Implementation Evolution

**Phase 1 — In-process**: `asyncio.Queue` per event type. Đơn giản, không
dependency ngoài.

**Phase 2 — RabbitMQ**: Proper message broker với persistence, acknowledgment,
routing (exchange/topic), dead-letter queue. Mỗi event type = 1 queue. Đảm bảo
at-least-once delivery + message replay khi cần. Đủ cho production.

**Tại sao RabbitMQ thay vì Redis**: Redis Pub/Sub không có persistence (message
mất nếu subscriber offline), không có ack (không biết đã xử lý thành công chưa),
không có dead-letter (message lỗi mất luôn). Trong hệ thống an ninh, mất 1 alert
fire là không chấp nhận được. RabbitMQ có đủ các guarantees này.

### 6.3 Guarantees

- **At-least-once delivery**: mỗi event được xử lý ít nhất 1 lần. Subscriber
  phải idempotent (xử lý 2 lần không gây alert trùng).
- **Ordering**: event từ cùng 1 camera được giữ thứ tự. Event khác camera có
  thể xử lý song song.
- **Back-pressure**: nếu subscriber chậm → event queue dài → publisher bị
  throttle (giảm sample rate) hoặc drop event ưu tiên thấp.

---

## 7. Chống Bỏ Sót — Defense In Depth

Đây là **yêu cầu quan trọng nhất**: "có lửa mà miss thì vứt". Hệ thống có 4
lớp phòng thủ chống miss:

| Layer | Cơ chế | Miss scenario được ngăn |
|---|---|---|
| **L1 — Motion gate** | Ngưỡng motion thấp (0.5% pixels) | Frame tĩnh thực sự mới skip |
| **L2 — Fire heuristic** | Chạy song song YOLO, alert ngay | Fire không có trong COCO class |
| **L3 — Persistence** | Cần N frame trong T giây | 1 frame nhiễu → không alert |
| **L4 — Escalation** | Alert tự động sau N giây không xử lý | VLM queue nghẽn → alert vẫn gửi |

**Nguyên tắc**: mỗi layer có thể false-positive (báo nhầm), nhưng khi cộng
hưởng → false-negative (bỏ sót) gần như không thể. Nếu 1 layer miss, layer
khác bắt.

---

## 8. Tối Ưu GPU — Chi Phí Và Giới Hạn

### 8.1 GPU Time Budget (RTX 3050 6GB)

```
Tổng GPU time có sẵn:      1000ms/s

Detection Engine:
  YOLO26n, batch 8:         ~20ms/batch (amortized 2.5ms/frame)
  Với 200 detections/s:     200 × 2.5ms = 500ms → 50% GPU

VLM Worker:
  Qwen 4B, 1 inference:     ~4s
  Với 6 calls/phút:         6000ms × 6 = 36000ms/phút → 60% GPU
  
Tổng: 50% + 60% = hơi vượt → cần điều chỉnh
→ Giảm sample FPS hoặc giảm VLM calls (rule chặt hơn)
→ Hoặc: detection engine chạy 1 FPS thay vì 2 FPS → 25% GPU
```

### 8.2 Chiến Lược Khi Quá Tải

```
1. Giảm sample FPS camera priority thấp (3→2, 2→1)
2. Merge VLM tasks cùng camera (thay vì 2 calls → 1 call với nhiều frame)
3. Skip VLM cho rule priority 4 (vehicle/object thông thường)
4. Drop frame detection ưu tiên thấp (giữ fire/smoke/person, bỏ object)
5. (Tuyệt đối không) drop alert HIGH hoặc fire detection
```

---

## 9. Ví Dụ: 10 Camera, 1 Phút

```
10 camera × 2 FPS × 60s = 1200 frame vào pipeline

Motion gate (ước tính 60% frame tĩnh):
  1200 × 0.4 = 480 frame vào Detection

Detection Engine (batch 8, 20ms/batch):
  480 / 8 = 60 batches × 20ms = 1.2s GPU time / phút = 2% GPU
  
Detection output: 480 events, trung bình 15-20 detection/frame có người

Rule Engine:
  Match rules → 3 camera có intrusion (persistence đạt)
              → 1 camera có crowd (5+ person)
              → 6 camera: có person nhưng bình thường
  → 4 VLM tasks được queue

VLM Worker:
  4 tasks × 4s = 16s GPU time / phút = 27% GPU
  
Tổng GPU: 2% + 27% = 29% → còn dư 71%
Kết luận: 10 camera dư sức trên RTX 3050
```
