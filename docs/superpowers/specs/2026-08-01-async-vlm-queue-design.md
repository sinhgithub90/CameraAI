# Async VLM Queue — Design Spec

> **Phạm vi**: Phase 1 Processing Plane — tách Detection & VLM thành 2 đường bất đồng bộ.
> **Ngày**: 2026-08-01.

## 1. Vấn Đề

Hiện tại `pipeline.analyze_event()` chạy đồng bộ: YOLO → VLM → response. Một
request VLM tốn 3-8 giây, trong thời gian đó event loop bị chiếm (dù đã có
`asyncio.to_thread`). Với N camera gửi frame cùng lúc, request thứ N phải chờ
request 1..N-1 hoàn thành. Không scale được.

## 2. Mục Tiêu

- **Detection trả về ngay** (< 1 giây): YOLO + motion → kết quả detection + alert tạm
- **VLM chạy nền** (3-8 giây sau): phân tích chuyên sâu → update alert
- **Không mất dữ liệu**: dù VLM queue đầy hay worker crash, alert vẫn tồn tại từ detection
- **API tương thích ngược**: endpoint cũ vẫn hoạt động, thêm endpoint mới cho async
- **0 dependency ngoài**: Phase 1 dùng `asyncio.Queue` in-process

## 3. Thiết Kế

### 3.1 Kiến Trúc

```
POST /analyze/image ──▶ pipeline.detect() ──▶ PipelineResult (ngay, <1s)
                                │                        │
                                │                        ├─ detections[]
                                │                        ├─ alert (tạm, level từ rule engine)
                                │                        └─ alert_id
                                │
                                ▼
                         VLMQueue.enqueue(VLMTask)
                                │
                                ▼
                         VLMWorker (background asyncio task)
                                │
                                ▼
                         pipeline.analyze_vlm(task)
                                │
                                ▼
                         AlertStore.update(alert_id, analysis)
                                │
                                ▼
                         GET /alerts/{alert_id} → kết quả đầy đủ
```

### 3.2 Components

#### Phân biệt: EventBus vs VLMQueue

Đây là **2 thứ khác nhau**, không gộp chung:

```
                      ┌─────────────┐
                      │  Event Bus  │  Pub/Sub — broadcast 1→nhiều
                      │  (topic)    │  "có chuyện gì đã xảy ra"
                      └──────┬──────┘
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
    Alert Service     Dashboard Push    Webhook Dispatcher
    (persist DB)      (WebSocket)       (HTTP outbound)

                     ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─

                      ┌─────────────┐
                      │  VLM Queue  │  Work Queue — phân phối 1→1
                      │  (priority) │  "ai rảnh thì làm task này"
                      └──────┬──────┘
                             │
                             ▼
                       VLM Worker
                       (chỉ 1 worker xử lý 1 task)
```

| | Event Bus | VLM Queue |
|---|---|---|
| **Pattern** | Pub/Sub — 1 message → nhiều consumer | Work queue — 1 task → 1 worker |
| **Dùng cho** | `alert.created`, `detection.completed`, `camera.offline` | `VLMTask` (fire, intrusion, crowd...) |
| **Consumer** | Nhiều subscriber nhận cùng 1 event | **1 worker duy nhất** pop & xử lý |
| **Phase 1 impl** | `InProcessEventBus` (asyncio.Queue per type) | `asyncio.PriorityQueue` |
| **Phase 2+ impl** | RabbitMQ Exchange (topic) + Queue per subscriber | RabbitMQ Queue (work queue, priority) |

**Tại sao phải tách**: Nếu gộp chung, alert.created bị VLM Worker pop mất → Alert Service
không nhận được → webhook không gửi, dashboard không update. Hoặc VLMTask bị broadcast
→ 3 worker cùng xử lý 1 task → gọi Qwen 3 lần → phí GPU.

**Dùng chung hạ tầng (Phase 2+ RabbitMQ), khác channel**:

```
RabbitMQ Server
  ├── Exchange: camera.events (topic)
  │     ├── Queue: alert.service    ← Alert Service
  │     ├── Queue: dashboard.push   ← WebSocket
  │     └── Queue: webhook.outbound ← Webhook Dispatcher
  │
  └── Queue: vlm.tasks (priority work queue)
        └── Consumer: VLM Worker × 1
```

#### EventBus

```python
class Event(ABC):
    type: str          # "alert.created", "detection.completed", ...
    source: str        # "rule_engine", "detector", ...
    camera_id: str
    timestamp: float
    payload: dict

class EventBus(ABC):
    async def publish(self, event: Event) -> None: ...
    async def subscribe(self, event_type: str, handler: Callable) -> None: ...
```

**InProcessEventBus**: `asyncio.Queue` cho mỗi event type. Phase 1.
**RabbitMQEventBus**: Exchange + Queue + Ack. Phase 2+.

#### VLMQueue

```python
class VLMQueue:
    async def enqueue(self, task: VLMTask, priority: int = 3) -> None: ...
    async def dequeue(self) -> VLMTask: ...
    @property
    def depth(self) -> int: ...
```

Bọc `asyncio.PriorityQueue`. Priority: 1=fire/smoke, 2=intrusion/fall, 3=crowd/loitering, 4=other.

**Priority động**: task chờ > 30s → effective_priority giảm 1; > 60s → giảm 2; > 120s → priority 1 (chống starvation).

#### VLMTask

```python
@dataclass
class VLMTask:
    task_id: str              # uuid
    camera_id: str
    alert_id: str             # alert đã tạo từ detection phase
    frames: list[np.ndarray]  # keyframes
    detections: list[Detection]
    rule_id: str              # rule kích hoạt
    priority: int             # 1-4
    enqueued_at: float        # timestamp
    prompt_template: str      # prompt per rule (Phase 2)
    max_keyframes: int
```

#### VLMWorker

```python
class VLMWorker:
    def __init__(self, queue: VLMQueue, pipeline: SecurityAIPipeline,
                 alert_store: AlertStore, event_bus: EventBus): ...
    async def run(self) -> None: ...   # loop vĩnh viễn: dequeue → analyze → update
    async def stop(self) -> None: ...
```

1 worker = 1 asyncio task chạy nền. VLM chỉ chạy 1 lần/lúc (1 GPU → 1 inference slot).
Sau này nhiều GPU → thêm worker instance.

#### AlertStore

```python
class AlertStore(ABC):
    async def create(self, alert: Alert) -> None: ...          # alert.id is already set
    async def update_vlm(self, alert_id: str, analysis: SceneAnalysis) -> None: ...
    async def get(self, alert_id: str) -> Alert | None: ...
    async def list_active(self, camera_id: str) -> list[Alert]: ...
```

**InMemoryAlertStore**: dict trong RAM. Phase 1 (mất khi restart — chấp nhận được).
**PostgreSQLAlertStore** (Phase 3): persist + query.

### 3.3 Pipeline Thay Đổi

`SecurityAIPipeline` được tách, giữ backward compat:

```python
class SecurityAIPipeline:
    # Giữ nguyên — đồng bộ, backward compat
    def analyze_event(self, event: EventObject) -> PipelineResult: ...
    
    # Mới: detect only — trả về ngay (YOLO + motion + gate)
    def detect(self, event: EventObject) -> PipelineResult: ...
    
    # Mới: VLM analysis cho 1 task đã có detection (sync — VLMWorker
    # gọi qua asyncio.to_thread để không block event loop)
    def analyze_vlm(self, task: VLMTask) -> SceneAnalysis: ...
```

**Không tạo type mới** — `PipelineResult` được tái sử dụng. Khi detect trả về:
- `vlm.status = "pending"` (trường mới trong `VLMResult`)
- `vlm.summary` rỗng, `security.alert_level` = mức tạm từ gate

Khi VLM xong → AlertStore update alert với `vlm.status = "completed"`.

**Schema change** — thêm 1 trường vào `VLMResult`:

```python
class VLMResult(BaseModel):
    summary: str
    observations: list[str] = Field(default_factory=list)
    degraded: bool = False
    skipped: bool = False
    status: str = "completed"  # MỚI: "pending" | "completed" | "skipped"
```

### 3.4 API Endpoints

| Endpoint | Mô tả |
|---|---|
| `POST /analyze/image` | **Giữ nguyên** — đồng bộ, backward compat |
| `POST /analyze/video` | **Giữ nguyên** — đồng bộ, backward compat |
| `POST /async/analyze/image` | **Mới** — detection ngay, VLM nền → trả về `alert_id` |
| `POST /async/analyze/video` | **Mới** — như trên cho video |
| `GET /alerts/{alert_id}` | **Mới** — poll trạng thái alert (pending → analyzed) |
| `GET /health` | **Giữ nguyên** — thêm queue depth |

### 3.5 Flow End-to-End

```
1. Client POST /async/analyze/image
2. pipeline.detect() → PipelineResult { request_id, detections[], vlm.status="pending" }
3. Response HTTP 200 + full PipelineResult (detection ngay, VLM sẽ chạy nền)
4. Background: VLMWorker dequeue → pipeline.analyze_vlm() → AlertStore.update()
5. Client GET /alerts/{alert_id} → { vlm: { status: "completed", summary: "..." } }
   (hoặc WebSocket push nếu có)
```

## 4. Non-Goals (cho Phase 1)

- Không có Rule Engine — vẫn dùng `VLMGate` đơn giản
- Không persistent queue — restart → mất task queue (nhưng alert đã lưu trong AlertStore)
- Không WebSocket push — client poll
- Không multi-tenant
- Không RabbitMQ — `InProcessEventBus` + `asyncio.Queue`

## 5. File Changes

| File | Thay đổi |
|---|---|
| `src/camera_ai/events.py` | **Mới** — Event, EventBus interface + InProcessEventBus |
| `src/camera_ai/queue.py` | **Mới** — VLMTask, VLMQueue (priority queue), VLMWorker |
| `src/camera_ai/alert_store.py` | **Mới** — AlertStore interface + InMemoryAlertStore, Alert model |
| `src/camera_ai/schemas.py` | **Sửa** — thêm `status: str` vào `VLMResult` |
| `src/camera_ai/pipeline.py` | **Sửa** — tách `detect()`, `analyze_vlm()` từ `analyze_event()`; extract `_read_video_frames()` helper |
| `apps/api/main.py` | **Sửa** — thêm `/async/analyze/*`, `GET /alerts/{id}`, khởi động VLMWorker lúc startup |
| `tests/` | **Mới** — `test_events.py`, `test_queue.py`, `test_alert_store.py`, `test_async_pipeline.py`; **Sửa** — `test_pipeline.py` (thêm test async methods)

**Frame lưu trong VLMTask.frames**: Với upload API (Phase 1), frame được decode từ request bytes, resize về 640px, lưu trực tiếp vào `VLMTask.frames` dạng numpy array. RAM ước tính ~5-20MB cho queue. Khi lên stream camera (Phase 4), frame lấy từ `FrameRingBuffer` per camera thay vì decode lại.

## 6. Rủi Ro

| Rủi ro | Mitigation |
|---|---|
| VLMWorker crash → mất task đang xử lý | Alert vẫn ở trạng thái "pending" → client poll thấy, operator vẫn thấy detection |
| Queue quá dài (VLM chậm hơn detection) | Priority động + escalation timer → alert quan trọng vẫn được xử lý |
| Memory leak (frame trong queue) | Frame được copy khi enqueue, clear sau khi VLM xong; max queue size |
| Frame buffer cho VLM context | VLMTask chứa frame đã sample sẵn, không đọc lại video |
