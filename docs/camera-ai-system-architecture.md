# Camera AI Platform — Kiến Trúc Hệ Thống

> **Loại tài liệu**: Target Architecture — bức tranh đích của hệ thống khi hoàn chỉnh.
> **Ngày**: 2026-08-01.
> **Trạng thái**: Draft.

Tài liệu này mô tả kiến trúc **mục tiêu** của Camera AI Platform — không bị
giới hạn bởi code hiện tại. Đây là bản thiết kế để hướng tới, chia thành các
phase triển khai ở cuối tài liệu.

---

## 1. Vision

Camera AI Platform là **nền tảng phân tích video an ninh tự động** dành cho
doanh nghiệp và tổ chức có nhu cầu giám sát qua camera IP.

**Giải quyết vấn đề gì**: Con người không thể theo dõi hàng trăm màn hình camera
24/7. Hệ thống thay thế việc "trực màn hình" bằng AI: phát hiện bất thường trong
vài giây, không bỏ sót, không mệt mỏi.

**Cho ai**: Doanh nghiệp, nhà xưởng, tòa nhà, bãi xe, khu dân cư — bất kỳ ai có
từ vài chục đến vài trăm camera IP.

**Quy mô mục tiêu**:
- **Camera**: 10 → 100+ camera IP mỗi deployment
- **Tenant**: 1 → 50+ khách hàng trên cùng hạ tầng (SaaS) hoặc deployment riêng (on-premise)
- **GPU**: 1 → N GPU, scale theo số camera
- **Độ trễ**: alert tức thì < 1 giây (detector); phân tích chuyên sâu < 10 giây (VLM)

**Nguyên tắc thiết kế cốt lõi**:
- **Rẻ lọc, đắt phân tích**: tầng rẻ (YOLO, motion) chạy liên tục mọi frame mọi camera; tầng đắt (VLM) chỉ chạy khi có tín hiệu
- **Không miss**: alert có thể false-positive, nhưng không được false-negative. "Báo nhầm còn hơn bỏ sót."
- **Nghiệp vụ mở rộng được**: thêm loại cảnh báo mới = config, không code
- **Deploy linh hoạt**: cùng 1 codebase, deploy được trên cloud (SaaS multi-tenant) hoặc on-premise (1 khách 1 server)

---

## 2. Năng Lực Hệ Thống

Hệ thống hoàn chỉnh cung cấp các năng lực sau:

### 2.1 Core — Phân Tích Video Thời Gian Thực

| Năng lực | Mô tả |
|---|---|
| **Ingest camera** | Kết nối camera IP qua RTSP/ONVIF/HTTP, quản lý connection, auto-reconnect |
| **Phát hiện chuyển động** | Frame diff trên CPU, lọc frame tĩnh trước GPU |
| **Object detection** | YOLO trên GPU — phát hiện người, xe, đồ vật... (COCO class + custom) |
| **Phát hiện đặc thù** | Fire/smoke detection (heuristic hoặc model nhẹ riêng) — alert ngay không chờ VLM |
| **Phân tích ngữ cảnh** | VLM (Qwen-VL) đánh giá tình huống an ninh: xâm nhập, ẩu đả, tụ tập, té ngã, vật cản... |
| **Face recognition** (future) | Nhận diện khuôn mặt cho danh sách đen/trắng |

### 2.2 Intelligence — Rule Engine

| Năng lực | Mô tả |
|---|---|
| **Rule-based alerting** | Mỗi nghiệp vụ = 1 rule, map detection → hành động |
| **Context-aware** | Cùng 1 detection, khác zone + thời gian → khác rule kích hoạt |
| **Persistence logic** | Phát hiện thoáng qua ≠ sự kiện thật; persistence counter + time window |
| **Escalation** | Alert chưa được xử lý → tự động nâng mức, notify kênh khác |
| **Rule template library** | Bộ rule mẫu cho các nghiệp vụ phổ biến: intrusion, fire, crowd, loitering, abandoned object... |

### 2.3 Management — Vận Hành Hệ Thống

| Năng lực | Mô tả |
|---|---|
| **Tenant management** | CRUD tenant, config feature flag, quota (số camera, retention) |
| **Camera registry** | Đăng ký camera mới, config protocol + credential + zone + schedule |
| **Rule editor** | Tạo/sửa/tắt rule theo tenant, kèm phiên bản & audit log |
| **User & role** | Admin tenant, operator (xem alert), installer (cấu hình camera) |
| **Health monitoring** | Camera online/offline, GPU usage, queue depth, VLM latency, disk usage |
| **Alert dashboard** | Real-time alert feed, lịch sử, filter, search, export |

### 2.4 Integration — Kết Nối Hệ Thống Ngoài

| Năng lực | Mô tả |
|---|---|
| **Webhook outbound** | Gửi alert đến hệ thống khách hàng: SMS gateway, Slack, Teams, tổng đài... |
| **WebSocket/SSE** | Push real-time đến dashboard và ứng dụng khách |
| **REST API** | Toàn bộ chức năng có API: alert history, camera status, system health |
| **ONVIF profile G/S** (future) | Tương tác chuẩn với camera/NVR: PTZ control, event subscription |

### 2.5 Data — Lưu Trữ & Phân Tích

| Năng lực | Mô tả |
|---|---|
| **Alert history** | Lưu mọi alert + detection + keyframe, search & filter |
| **Video clip retention** | Lưu clip gốc của alert trong N ngày (config theo tenant) |
| **Audit log** | Mọi thay đổi config, mọi alert resolution |
| **Analytics** | Thống kê: số alert theo loại/camera/thời gian, false positive rate, response time |
| **Feedback loop** | Operator đánh dấu alert đúng/sai → cải thiện rule threshold, prompt template |

---

## 3. Kiến Trúc Tổng Thể

Hệ thống chia thành **4 khối chính** (planes):

```
┌────────────────────────────────────────────────────────────────────┐
│                     CAMERA AI PLATFORM                             │
│                                                                    │
│  ┌─────────────────────── MANAGEMENT PLANE ──────────────────────┐ │
│  │  Tenant Admin │ Camera Registry │ Rule Editor │ User & Role   │ │
│  │  Health Monitor │ Audit Log │ Analytics │ Billing (future)    │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                                                                    │
│  ┌─────────────────────── PROCESSING PLANE ──────────────────────┐ │
│  │                                                                │ │
│  │  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   │ │
│  │  │ Ingestion│──▶│ Detection│──▶│  Rule    │──▶│   VLM    │   │ │
│  │  │ Gateway  │   │ Engine   │   │ Engine   │   │  Worker  │   │ │
│  │  │          │   │ (YOLO)   │   │          │   │ (Qwen)   │   │ │
│  │  └──────────┘   └──────────┘   └──────────┘   └──────────┘   │ │
│  │       │               │               │               │       │ │
│  │  ┌────▼───────────────▼───────────────▼───────────────▼────┐  │ │
│  │  │                    EVENT BUS                             │  │ │
│  │  └─────────────────────────────────────────────────────────┘  │ │
│  └────────────────────────────────────────────────────────────────┘ │
│                                                                    │
│  ┌─────────────────────── DATA PLANE ────────────────────────────┐ │
│  │  Alert Store │ Frame Storage │ Config DB │ Analytics DB (OLAP)│ │
│  └───────────────────────────────────────────────────────────────┘ │
│                                                                    │
│  ┌─────────────────────── INTEGRATION PLANE ──────────────────────┐ │
│  │  REST API │ WebSocket/SSE │ Webhook Dispatcher │ ONVIF (fut.)  │ │
│  └───────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘
```

### 3.1 Processing Plane — "Bộ Não"

Đây là trái tim hệ thống, nơi frame từ camera được xử lý liên tục.

```
Camera IP ──▶ [Ingestion Gateway] ──▶ [Detection Engine] ──▶ [Rule Engine]
                 │                         │                      │
                 │ Mở RTSP/HTTP/RTC        │ YOLO GPU             │ Match rule
                 │ Sample 1-5 fps          │ Motion gate          │ Alert ngay
                 │ Buffer N giây frame     │ Batch N camera       │ Queue VLM
                 │                         │                      │
                 │                         ▼                      ▼
                 │                    ┌──────────────────────────────────┐
                 │                    │          EVENT BUS               │
                 └────────────────────│  (async, in-proc → Redis/PG)     │
                                      └────────────┬─────────────────────┘
                                                   │
                              ┌────────────────────┼────────────────────┐
                              ▼                    ▼                     ▼
                       [VLM Worker]        [Alert Service]        [Frame Storage]
                        Qwen GPU            Persist + Push         Save keyframes
```

**Nguyên lý hoạt động**:

1. **Ingestion Gateway** mở connection tới từng camera (RTSP pull hoặc nhận HTTP push), sample frame theo interval (1-5 FPS tùy camera priority), chạy motion detection trên CPU để lọc frame tĩnh. Frame có chuyển động được đẩy tiếp. Frame gần nhất của mỗi camera được giữ trong ring buffer (RAM) để phục vụ VLM sau này.

2. **Detection Engine** nhận frame từ nhiều camera, gom batch (4-8 frame), chạy YOLO trên GPU. Kết quả detection + frame metadata được publish lên Event Bus. Ngoài YOLO, có thể cắm thêm detector đặc thù (fire/smoke heuristic, face detection...) chạy song song. Detection Engine là tầng rẻ duy nhất chạy liên tục — GPU time cho tầng này phải < 30% tổng năng lực GPU.

3. **Rule Engine** subscribe Event Bus, nhận detection, match với rule của tenant tương ứng. Mỗi rule quyết định:
   - Có tạo alert ngay không? Mức gì?
   - Có cần VLM phân tích không? Priority? Strategy chọn frame? Prompt template nào?
   - Dedup & cooldown: sự kiện lặp trong N giây → update alert cũ, không tạo mới.

4. **VLM Worker**(s) nhận task từ Rule Engine qua priority queue. Mỗi worker = 1 GPU inference slot. Worker lấy frame từ buffer theo strategy được chỉ định, gọi Qwen-VL, publish kết quả về Event Bus. VLM Worker là tài nguyên khan hiếm nhất — mọi tối ưu (batch, priority, frame selection) tập trung vào đây.

5. **Event Bus** là backbone giao tiếp bất đồng bộ. Phase đầu: in-process (asyncio.Queue). Phase sau: external (Redis Pub/Sub hoặc PostgreSQL LISTEN/NOTIFY) để scale multi-process.

### 3.2 Management Plane — "Bảng Điều Khiển"

Nơi người vận hành (admin tenant, operator) tương tác với hệ thống:

- **Tenant Admin**: tạo tenant mới, set quota (số camera, retention ngày), bật/tắt feature (VLM, webhook, analytics)
- **Camera Registry**: đăng ký camera (URL, protocol, credential, zone), test connection, bật/tắt/remove
- **Rule Editor**: xem/sửa/tạo rule cho tenant. Template library cho các nghiệp vụ phổ biến. Audit log mọi thay đổi
- **User & Role**: quản lý người dùng trong tenant (admin, operator, viewer), phân quyền
- **Health Monitor**: dashboard trạng thái: camera online/offline, GPU usage %, queue depth, VLM latency p50/p99, disk usage, alert rate

### 3.3 Data Plane — "Kho Dữ Liệu"

- **Config DB** (PostgreSQL): tenant, camera, rule, user — dữ liệu cấu hình, ít thay đổi
- **Alert Store** (PostgreSQL): alerts + detections + VLM analysis — truy vấn thường xuyên, cần index theo tenant_id + camera_id + thời gian
- **Frame Storage** (disk / object storage): keyframe JPEG của alert, clip gốc ngắn (5-30s). Retention policy theo tenant (mặc định 30 ngày, tự động xóa)
- **Analytics DB** (PostgreSQL hoặc ClickHouse — future): dữ liệu thống kê, aggregate theo ngày/tuần/tháng. Tách khỏi operational DB để không ảnh hưởng performance

### 3.4 Integration Plane — "Cổng Kết Nối"

- **REST API**: toàn bộ chức năng qua HTTP — alert history, camera CRUD, rule CRUD, system health
- **WebSocket/SSE**: push alert real-time đến dashboard. Filter theo tenant
- **Webhook Dispatcher**: HTTP POST alert đến URL của khách hàng. Retry với backoff. Queue riêng để không block Event Bus
- **Auth Gateway**: JWT-based authentication. API key cho webhook outbound. Mỗi tenant có secret riêng

---

## 4. Core Abstractions

Đây là những khái niệm nền tảng mà toàn bộ hệ thống xoay quanh.

### 4.1 Tenant

Đơn vị cô lập dữ liệu. Mọi entity đều thuộc về 1 tenant.

```
Tenant {
  id, name,
  quota: { max_cameras, retention_days, vlm_enabled, analytics_enabled },
  config: { timezone, language },
  created_at, status
}
```

### 4.2 Camera

1 camera IP thuộc 1 tenant, gắn với 1 zone.

```
Camera {
  id, tenant_id, name,
  source: { protocol, url, credential_encrypted },
  zone: null | "front_gate" | "parking" | "warehouse" | ...,
  priority: 1-5,              // ảnh hưởng sample FPS
  schedule: { active_hours, fps_active, fps_idle },
  enabled_rule_ids: [...],    // rule áp dụng cho camera này
  status: online | offline | disabled,
  last_seen_at
}
```

### 4.3 Zone

Khu vực không gian trong 1 tenant. Zone là context để Rule Engine phân biệt: `person` trong `restricted_zone` lúc 2h sáng → nguy hiểm, nhưng `person` trong `lobby` lúc 14h → bình thường.

Zone có thể được định nghĩa bằng:
- **Tag tĩnh**: operator gán zone cho camera (vd "cổng chính", "bãi xe")
- **Polygon trên frame** (future): vẽ vùng trên ảnh camera, YOLO check xem object có nằm trong vùng không

### 4.4 Rule = Nghiệp Vụ An Ninh

1 rule = 1 kịch bản an ninh hoàn chỉnh, bao gồm: **khi nào kích hoạt**, **làm gì khi kích hoạt**.

```yaml
rule:
  id: intrusion_detection
  tenant_id: acme_corp
  name: "Phát hiện xâm nhập"
  description: "Người trong khu vực cấm ngoài giờ hành chính"
  
  trigger:
    detections: [person]
    confidence_min: 0.6
    zone_in: [restricted_area, back_entrance]
    schedule:
      after: "22:00"
      before: "06:00"
    persistence: 3              # ≥ 3 frame trong time_window mới kích hoạt
    time_window: 30s
  
  action:
    alert_immediately: true     # alert ngay, không chờ VLM
    alert_level: medium
    vlm:
      enabled: true
      priority: 2               # 1=khẩn, 2=cao, 3=thường
      strategy: event_aware     # event_aware | spread | cluster
      max_keyframes: 8          # số frame gửi Qwen
      prompt_template: intrusion_prompt
    cooldown: 120s              # không tạo alert mới cùng camera+rule trong 120s
    escalate_after: 300s        # nếu alert chưa được resolve sau 5ph → nâng level
    notify:
      - channel: websocket
      - channel: webhook
        url: "https://khach.com/alerts"
```

**Rule template library** — bộ rule mẫu đóng gói sẵn:

| Template | Trigger | VLM? | Mức |
|---|---|---|---|
| `fire_smoke` | fire/smoke detection | Optional enrich | HIGH ngay |
| `intrusion` | person + restricted zone + ngoài giờ | Có — xác nhận xâm nhập | MEDIUM → HIGH |
| `crowd_gathering` | ≥ 5 person trong cùng zone | Có — đánh giá mức độ | MEDIUM |
| `loitering` | person hiện diện > 5 phút trong zone | Có — xác nhận lảng vảng | LOW → MEDIUM |
| `vehicle_unauthorized` | car/truck + restricted zone | Có — đọc biển số (future) | MEDIUM |
| `abandoned_object` | static object > 5 phút, không phải người | Có — phân tích vật thể | MEDIUM |
| `fall_detection` | person + bbox thay đổi đột ngột (nằm ngang) | Có — xác nhận té ngã | HIGH |
| `camera_tampered` | frame đen/mờ/che khuất > 10s | Không | HIGH ngay |

### 4.5 Alert — Vòng Đời

```
TẠO (Rule Engine match)
 │
 ├─ status=active, level=initial
 │
 ├─ [VLM phân tích xong] → level có thể nâng/hạ, thêm analysis JSON
 │
 ├─ [Escalation] → level tự động tăng nếu chưa resolved sau N giây
 │
 ├─ [Cooldown] → detection lặp trong cooldown window → update alert hiện tại
 │
 └─ RESOLVE (operator hoặc tự động khi hết tín hiệu)
      │
      ├─ resolution = "Bảo vệ đã kiểm tra: báo đúng"
      └─ resolution = "Báo nhầm — chim bay qua" → thống kê false positive
```

```
Alert {
  id, tenant_id, camera_id, rule_id,
  started_at, resolved_at,
  initial_level, confirmed_level,           // low | medium | high
  vlm_analysis: { summary, observations, risks, recommended_action } | null,
  keyframe_urls: [...],
  detection_summary: { person: 12, car: 3 }, // tổng detection trong alert window
  status: active | resolved,
  resolution: string | null,
  resolved_by: user_id | "auto" | null,
  created_at
}
```

### 4.6 Event — Ngôn Ngữ Chung

Mọi thành phần giao tiếp qua Event — một message bất biến trên Event Bus.

```
Event {
  id, type, source, tenant_id, camera_id,
  timestamp, payload: {...}
}
```

Các event type chính: `frame.captured`, `detection.completed`, `rule.matched`, `alert.created`, `alert.vlm_confirmed`, `alert.resolved`, `camera.offline`, `camera.online`, `system.health`.

---

## 5. Cross-Cutting Concerns

### 5.1 Multi-Tenancy

**Isolation model**: **Shared database, Row-Level Security (RLS)**.
- Mọi bảng có `tenant_id`. Mọi query có `WHERE tenant_id = $1`.
- PostgreSQL RLS làm lớp bảo vệ thứ 2: tenant A không thể SELECT rows của tenant B dù query bị lỗi.
- **Sẵn sàng nâng cấp**: nếu sau này cần isolation mạnh hơn → mỗi tenant 1 PostgreSQL schema, hoặc 1 database riêng. Không thay đổi code nhờ abstraction layer ở data access.

**Quota & Fairness**:
- Mỗi tenant có quota: số camera tối đa, retention ngày, VLM request/giờ.
- GPU scheduler đảm bảo tenant này không chiếm hết GPU của tenant khác (weighted fair queueing).

### 5.2 Security

| Layer | Mechanism |
|---|---|
| API auth | JWT (access + refresh token). API key cho machine-to-machine |
| Camera credential | AES-256 encrypted at rest, chỉ decrypt trong memory khi mở connection |
| Webhook signature | HMAC-SHA256 — khách hàng verify request đến từ hệ thống |
| Audit log | Immutable log mọi thay đổi config + alert resolution |
| Network | Camera thường ở LAN/VLAN riêng — hệ thống phải nằm trong network tiếp cận được |

### 5.3 GPU Resource Management

GPU là tài nguyên khan hiếm và đắt nhất. Chiến lược quản lý:

- **Tiered scheduling**: Detection Engine dùng 30% GPU time (chạy liên tục). VLM Worker dùng 70% còn lại. Partition này có thể điều chỉnh.
- **Detection batching**: thay vì 1 frame/lần, gom 4-8 frame từ nhiều camera → 1 lần inference phục vụ nhiều nguồn.
- **VLM priority queue**: fire/smoke > intrusion > loitering. Priority động: task chờ > N giây tự tăng.
- **VLM keep-alive**: model Qwen giữ trong VRAM (không unload giữa các request) để tránh reload penalty.
- **Multi-GPU**: N GPU = N VLM worker song song + 1 GPU chuyên detection (hoặc chia sẻ). Load balancing qua queue.
- **Graceful degradation**: nếu GPU quá tải → giảm sample FPS camera thấp, skip VLM task priority thấp, nhưng **không bao giờ skip alert khẩn**.

### 5.4 Observability

| Metric | Mô tả |
|---|---|
| `detection_latency_ms` | p50/p95/p99 — thời gian YOLO/inference |
| `vlm_queue_depth` | Số task đang chờ trong VLM queue |
| `vlm_latency_ms` | p50/p95/p99 — thời gian Qwen/inference |
| `camera_fps_actual` | FPS thực tế của từng camera |
| `alerts_per_hour` | Số alert tạo ra/giờ, theo tenant, theo rule |
| `false_positive_rate` | % alert bị mark là báo nhầm, theo rule |
| `gpu_utilization_pct` | % GPU usage, memory used |
| `disk_usage_pct` | % disk đã dùng cho frame storage |

Health endpoint: `GET /health` trả về trạng thái tất cả subsystem (DB, GPU, queue, camera connections).

### 5.5 Storage & Retention

- **Hot storage** (SSD/NVMe): frame buffer ring (vài phút gần nhất trong RAM), keyframe của alert đang active
- **Warm storage** (HDD): keyframe + clip của alert đã resolved, giữ 7-90 ngày tùy tenant config
- **Cold storage** (optional, future): archive alert cũ lên S3/MinIO cho compliance
- **Cleanup job**: chạy định kỳ xóa frame/clip hết hạn retention

---

## 6. Deployment Architecture

### 6.1 On-Premise (1 khách = 1 server)

```
┌─────────────────────────────────────────┐
│           Customer Server (GPU)          │
│                                          │
│  ┌────────────────────────────────────┐  │
│  │         Docker Compose              │  │
│  │                                     │  │
│  │  camera-ingestion    :1 container   │  │
│  │  detection-engine    :1 container   │  │
│  │  vlm-worker          :1 container   │  │
│  │  api-server          :1 container   │  │
│  │  postgresql          :1 container   │  │
│  │  ollama              :1 container   │  │
│  └─────────────────────────────────────┘  │
│                                            │
│  GPU ── shared between detection + vlm     │
│  Storage ── local disk cho frames          │
└────────────────────────────────────────────┘
```

### 6.2 Cloud SaaS (Multi-Tenant)

```
                         ┌──────────────┐
                         │  LB / Nginx  │
                         └──────┬───────┘
                                │
         ┌──────────────────────┼──────────────────────┐
         │                      │                      │
  ┌──────▼──────┐      ┌───────▼──────┐      ┌───────▼──────┐
  │ Ingestion   │      │  API Server  │      │  API Server  │
  │ Gateway × N │      │   × 2        │      │   × 2        │
  └──────┬──────┘      └──────────────┘      └──────────────┘
         │
  ┌──────▼──────┐      ┌──────────────┐      ┌──────────────┐
  │ Detection   │      │   Redis /    │      │ PostgreSQL   │
  │ Engine × M  │      │   PG Queue   │      │ (primary +   │
  │ (mỗi cái    │      │              │      │  replica)    │
  │  1 GPU)     │      └──────────────┘      └──────────────┘
  └──────┬──────┘
         │
  ┌──────▼──────┐
  │ VLM Worker  │
  │ × K (1 GPU  │
  │ mỗi worker) │
  └─────────────┘
```

Scale ngang: thêm server GPU → thêm Detection Engine + VLM Worker instance. Queue (Redis/PG) làm buffer giữa các tầng.

### 6.3 Edge + Cloud (Future)

- **Edge box** (Jetson Orin / Raspberry Pi + Coral TPU) tại site khách hàng: chạy Ingestion + Motion + YOLO nhẹ. Frame chỉ upload lên cloud khi có tín hiệu cần VLM.
- **Cloud**: VLM Worker + Alert Service + Dashboard. Tiết kiệm băng thông (không upload 24/7) và GPU cloud (chỉ xử lý frame có event).
- Phù hợp khách hàng có nhiều site nhỏ, không muốn đầu tư GPU server tại chỗ.

---

## 7. Technology Principles

| Lựa chọn | Lý do |
|---|---|
| **Python** (backend core) | Hệ sinh thái AI/ML phong phú nhất; ultralytics, opencv, fastapi |
| **PostgreSQL** (primary DB) | Đủ mạnh cho cả config + alerts; RLS cho multi-tenant |
| **RabbitMQ** (event bus + task queue) | Persistence, ack, dead-letter, routing — bắt buộc cho hệ thống an ninh không được mất message |
| **Ollama** (model serving) | Local-first, không cần cloud; API chuẩn; đủ cho inference 1-2 GPU |
| **Ultralytics YOLO** | YOLO26n nhanh (15ms), hệ sinh thái trưởng thành, dễ fine-tune |
| **Qwen-VL** (VLM) | Open-weight, tiếng Việt tốt, Ollama hỗ trợ chính thức |
| **Docker Compose** (deploy) | Đơn giản cho on-premise; K8s khi scale cloud |
| **FFmpeg** (RTSP ingest) | Chuẩn công nghiệp cho video streaming |
| **Không tự build**: auth (dùng JWT chuẩn + thư viện), queue (dùng PG/Redis, không tự viết), object storage (dùng MinIO/S3), monitoring (Prometheus + Grafana) |

---

## 8. Lộ Trình Tiến Hóa

Kiến trúc này không build trong 1 lần. Đây là lộ trình 4 giai đoạn, mỗi giai đoạn đưa hệ thống tiến gần hơn đến kiến trúc đích.

### Phase 1 — Async Pipeline (in-process)

**Mục tiêu kiến trúc**: Tách luồng xử lý thành 2 đường độc lập — detect trả ngay, VLM chạy nền.

**Phạm vi**:
- Detector trả `PipelineResult` ngay sau YOLO (có alert tạm nếu có detection)
- VLM chạy bất đồng bộ qua `asyncio.Queue`, update alert khi xong
- Frame buffer ring trong RAM cho mỗi camera
- Priority queue cơ bản (fire > person > khác)
- API giữ nguyên interface `/analyze/*`, thêm WebSocket `/ws` cho real-time update

**Ý nghĩa kiến trúc**: Đây là bước quan trọng nhất — phá vỡ mô hình đồng bộ, mở đường cho mọi thứ sau này. Không có bước này, không thể scale nhiều camera.

### Phase 2 — Rule Engine

**Mục tiêu kiến trúc**: Tách nghiệp vụ khỏi code — thêm nghiệp vụ mới = thêm config.

**Phạm vi**:
- Rule data model + Rule Engine (match detection → action)
- Rule config file (YAML) → load lúc startup
- VLM prompt template per rule
- Frame selection strategy per rule
- `VLMGate` cũ → replaced by Rule Engine
- Rule template library (fire, intrusion, crowd, loitering)
- Fire/smoke detection ở tầng rẻ (quyết định heuristic hay model nhẹ)

**Ý nghĩa kiến trúc**: Mở rộng nghiệp vụ mà không sửa core. Đây là điều kiện để onboard nhiều khách hàng với nhu cầu khác nhau.

### Phase 3 — Multi-Tenant Data Model + Alert Lifecycle

**Mục tiêu kiến trúc**: Data model sẵn sàng cho nhiều tenant; alert có vòng đời đầy đủ.

**Phạm vi**:
- Migration: thêm `tenant_id` vào tất cả bảng
- Alert CRUD API + lịch sử + search
- Alert lifecycle: active → VLM confirm → escalate → resolve
- Dedup & cooldown logic
- Webhook dispatcher (outbound HTTP)
- Health monitor cơ bản (camera online/offline, queue depth, GPU)

**Ý nghĩa kiến trúc**: Từ "tool phân tích ảnh" thành "nền tảng vận hành". Có tenant_id từ đầu → không phải migration đau sau này.

### Phase 4 — Full Platform

**Mục tiêu kiến trúc**: Hệ thống hoàn chỉnh như mô tả trong kiến trúc này.

**Phạm vi**:
- RTSP Ingestion Gateway (connection pool, reconnect, health check)
- Multi-camera scheduler (priority-based sampling)
- Tenant admin UI (camera CRUD, rule editor, user management)
- Observability stack (Prometheus metrics, Grafana dashboard)
- Docker Compose deployment package cho on-premise
- Performance tuning: batch detection, VLM batching, storage cleanup
- Documentation: operator manual, deployment guide, API reference

---

## 9. Quyết Định Kiến Trúc Cần Chốt

| # | Quyết định | Lựa chọn | Trạng thái |
|---|---|---|---|
| 1 | Fire/smoke detection ở tầng rẻ? | Heuristic màu (đơn giản, false-positive chấp nhận được) hoặc model YOLO fire nhẹ | **Cần quyết định** |
| 2 | Event Bus: in-proc → RabbitMQ khi nào? | asyncio.Queue (Phase 1) → RabbitMQ (Phase 2+) | **Đã chốt** |
| 3 | Multi-tenant isolation level? | Row-Level Security (RLS) → schema-per-tenant nếu cần mạnh hơn | RLS |
| 4 | Frame storage: local disk hay object storage? | Local disk cho on-premise; MinIO/S3 adapter cho cloud | Local trước, adapter sau |
| 5 | Deployment: Docker Compose hay Kubernetes? | Compose cho on-premise đơn giản; Helm chart khi cần K8s | Compose trước |
