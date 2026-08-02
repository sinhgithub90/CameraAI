# RabbitMQ Transport Adapters — Design Spec

> **Phạm vi:** Viết sẵn RabbitMQ adapters và contract dùng chung; runtime hiện tại vẫn chạy hoàn toàn in-process.
> **Ngày:** 2026-08-01.

## 1. Mục tiêu

Hệ thống có hai kiểu truyền thông khác nhau:

- `EventBus`: broadcast một event đến mọi subscriber quan tâm.
- `TaskQueue[T]`: giao một task cho đúng một worker trong một consumer group.

Cả hai có implementation in-process và RabbitMQ, nhưng business logic chỉ phụ
thuộc vào interface. RabbitMQ dùng chung connection infrastructure, codec,
logging và health checks; hai abstraction không bị gộp vì delivery semantics
khác nhau.

Implementation lần này là code chuẩn bị sẵn. `apps/api/main.py` tiếp tục khởi
tạo backend in-process và không kết nối RabbitMQ khi ứng dụng khởi động.

## 2. Ngoài phạm vi

- Không cài đặt hoặc khởi chạy RabbitMQ trên máy test.
- Không đổi backend mặc định của API.
- Không gửi JPEG, base64, `numpy.ndarray` hoặc `RawVideoWindow` qua broker.
- Không tách Detection Engine, Rule Engine hay VLM Worker thành service riêng.
- Không triển khai PostgreSQL/Object Storage/MinIO trong thay đổi này.
- Không mô phỏng dynamic priority aging của `VLMQueue` bằng topology RabbitMQ.
- Không thay đổi thuật toán motion, detector, candidate routing hoặc Qwen.

## 3. Quyết định kiến trúc

### 3.1 Chung hạ tầng, tách abstraction

```text
RabbitMQConnection
├── RabbitMQEventBus
│   └── topic exchange → queue riêng cho mỗi subscriber identity
└── RabbitMQTaskQueue[T]
    └── durable work queue → competing consumers
```

`EventBus` không được dùng để phân phối VLM task. `TaskQueue` không được dùng
để broadcast alert/detection event.

### 3.2 Backend được chọn độc lập

Các setting dự kiến:

```text
CAMERA_AI_EVENT_BUS_BACKEND=inprocess|rabbitmq
CAMERA_AI_VLM_QUEUE_BACKEND=inprocess|rabbitmq
RABBITMQ_URL=amqp://guest:guest@localhost/
```

Trong deliverable này, factory và config được test độc lập nhưng API vẫn hard-code
backend in-process. Việc nối factory vào startup là một deployment change riêng.

### 3.3 Optional dependency

RabbitMQ client dùng `aio-pika` robust connection. Dependency nằm trong optional
extra `rabbitmq`, không buộc môi trường in-process cài Erlang, RabbitMQ hoặc
`aio-pika`. Module RabbitMQ phải lazy-import và chỉ báo lỗi cấu hình rõ ràng khi
người gọi thật sự chọn backend RabbitMQ.

## 4. Contract dùng chung

### 4.1 Message envelope

Mọi message transport-safe có envelope versioned:

```python
@dataclass(frozen=True)
class MessageEnvelope:
    message_id: str
    message_type: str
    source: str
    camera_id: str
    occurred_at: datetime
    correlation_id: str | None
    causation_id: str | None
    schema_version: int
    payload: Mapping[str, JSONValue]
```

Codec JSON chỉ chấp nhận JSON-native values. Payload chứa bytes, ndarray hoặc
object tùy ý phải fail trước khi publish. Envelope được encode UTF-8 với content
type `application/json`.

`Event` hiện tại được giữ tương thích ở public API; mapper chuyển `Event` sang
envelope và ngược lại. Mỗi decode tạo object mới, không chia sẻ mutable payload
giữa subscriber như implementation hiện tại.

### 4.2 EventBus

```python
class EventBus(ABC):
    async def publish(self, event: Event) -> None: ...
    async def subscribe(
        self,
        event_type: str,
        handler: EventHandler,
        *,
        subscriber_id: str | None = None,
    ) -> None: ...
    async def close(self) -> None: ...
```

- Hai đối số cũ của `subscribe(event_type, handler)` tiếp tục chạy.
- `subscriber_id=None` nghĩa là subscription sống theo process, phù hợp dev/test.
- RabbitMQ production subscriber phải cung cấp identity ổn định, ví dụ
  `rule-engine` hoặc `alert-store`, để có durable queue riêng.
- `InProcessEventBus` tạo private bounded queue cho từng subscription.
- Exact event type được hỗ trợ ở cả hai backend. Rabbit backend có thể nhận topic
  pattern; in-process backend phải match `*` và `#` với cùng semantics.

### 4.3 TaskQueue và Delivery

```python
class Delivery(Generic[T]):
    task: T
    attempt: int
    async def ack(self) -> None: ...
    async def retry(self) -> None: ...
    async def reject(self) -> None: ...

class TaskQueue(ABC, Generic[T]):
    async def enqueue(self, task: T, *, priority: int = 3) -> None: ...
    async def receive(self) -> Delivery[T]: ...
    async def close(self) -> None: ...
```

Delivery chỉ được kết thúc một lần. Gọi `ack/retry/reject` lần hai là lỗi contract.
Worker ACK sau khi toàn bộ side effects bắt buộc đã hoàn thành. Lỗi transient gọi
`retry`; lỗi validation/permanent gọi `reject`.

`InProcessTaskQueue` bọc `asyncio.PriorityQueue`; `retry` đưa task trở lại queue và
tăng attempt. `RabbitMQTaskQueue` dùng manual acknowledgement, publisher confirms
và `prefetch=1` mặc định cho workload VLM.

`VLMQueue` hiện tại được giữ như adapter/alias tương thích trên
`InProcessTaskQueue[VLMTask]`. `VLMWorker` chuyển sang xử lý `Delivery[VLMTask]`
nhưng behavior thành công không đổi.

## 5. RabbitMQ topology

### 5.1 EventBus topology

```text
exchange: camera_ai.events (topic, durable)

subscriber rule-engine:
  queue: camera_ai.events.rule-engine
  binding: detection.*

subscriber alert-store:
  queue: camera_ai.events.alert-store
  binding: alert.*
```

Một event được publish một lần lên exchange. Exchange route bản sao logic đến
từng subscriber queue. Nhiều process có cùng `subscriber_id` là competing
consumers của cùng service; khác `subscriber_id` nhận độc lập.

### 5.2 TaskQueue topology

```text
exchange: camera_ai.tasks (direct, durable)
queue: camera_ai.tasks.vlm (durable, priority)
routing key: vlm
dead-letter exchange: camera_ai.dead
dead-letter queue: camera_ai.tasks.vlm.dlq
```

Số priority là low single digit. Domain giữ quy ước số nhỏ là khẩn hơn; RabbitMQ
adapter đảo sang AMQP priority vì RabbitMQ dùng số lớn là ưu tiên cao hơn.

### 5.3 Reliability

- Persistent messages + durable exchanges/queues.
- Publisher confirms và `mandatory=True` để phát hiện unroutable message.
- Manual consumer ACK sau handler/worker side effects.
- Mất connection trước ACK làm task được broker redeliver.
- Consumer phải idempotent theo `message_id`/`task_id` vì delivery là at-least-once.
- `reject` đưa message vào DLQ.
- `retry` có giới hạn attempt; khi vượt giới hạn chuyển DLQ, không requeue vô hạn.
- Retry của EventBus là subscriber-local; không publish lại vào topic exchange làm
  các subscriber đã thành công nhận trùng.

## 6. VLM task và giới hạn serialization

`VLMTask` hiện chứa:

```python
frames: list[np.ndarray]
raw_window: RawVideoWindow | None
```

Do đó `RabbitMQTaskQueue[VLMTask]` chưa được nối vào runtime. Rabbit adapter là
generic và nhận một `TaskCodec[T]`; contract/integration tests dùng DTO JSON nhỏ.
Khi triển khai multi-process thật, thay đổi kế tiếp phải bổ sung `FrameStore` và
DTO `VLMJob` chỉ chứa `frame_refs/window_ref`, detection summary, candidate,
priority và IDs. Worker hydrate dữ liệu từ store trước inference.

Nếu code cố encode `VLMTask` hiện tại bằng JSON codec, publish phải fail fast với
lỗi serialization; tuyệt đối không fallback sang pickle hoặc base64.

## 7. Error handling và lifecycle

- Robust connection tự reconnect và redeclare durable topology.
- `close()` idempotent, hủy consumers/channels rồi đóng connection.
- Handler exception không bị nuốt im lặng: log đầy đủ message ID, event type,
  subscriber ID và attempt.
- In-process queue có `maxsize` cấu hình được. Queue đầy tạo back-pressure bằng
  cách block publisher, không tăng RAM vô hạn.
- Malformed JSON, schema version không hỗ trợ hoặc payload sai schema bị reject
  vào DLQ, không retry.
- Secret trong `RABBITMQ_URL` không được ghi vào log.

## 8. Testing strategy

### 8.1 Contract tests không cần RabbitMQ

Chạy cho in-process implementations:

- Event fan-out, exact routing và wildcard routing.
- Subscriber độc lập và cleanup khi cancel.
- Bounded queue/back-pressure.
- Task priority, competing consumers và delivery single-finalization.
- ACK, retry, reject và max-attempt behavior.
- JSON envelope round-trip và rejection của ndarray/bytes.

### 8.2 Rabbit adapter unit tests

Mock `aio-pika` boundary để xác nhận:

- Durable exchange/queue declarations và bindings đúng.
- Persistent delivery mode, content type, message ID, priority mapping.
- Manual ACK/reject và publisher confirms.
- `subscriber_id` tạo stable queue name.
- Optional dependency/lazy import không ảnh hưởng default test suite.

### 8.3 Opt-in integration tests

Integration tests chỉ chạy khi có `RABBITMQ_TEST_URL`. Không có biến này thì
skip, không tự khởi Docker hoặc kết nối localhost. Test xác nhận fan-out,
competing consumers, reconnect/redelivery và DLQ trên broker thật.

### 8.4 Regression tests

Toàn bộ test pipeline hiện tại phải pass với backend in-process. API startup
không được import hoặc kết nối `aio-pika` trong cấu hình mặc định.

## 9. Phân chia công việc song song

Sau khi contract/envelope được merge, các nhánh có thể làm độc lập:

1. Nhánh A: harden `InProcessEventBus` + `InProcessTaskQueue` theo contract.
2. Nhánh B: shared JSON codec, RabbitMQ connection/config và `RabbitMQEventBus`.
3. Nhánh C: `RabbitMQTaskQueue`, priority/ACK/retry/DLQ và opt-in integration tests.

Nhánh B và C không sửa business pipeline. Chỉ nhánh A refactor `VLMWorker` sang
delivery contract, vì default backend vẫn in-process và regression tests bảo vệ
behavior hiện tại.

## 10. Tiêu chí hoàn thành

- Default API vẫn dùng in-process và không cần RabbitMQ/aio-pika.
- `EventBus` và `TaskQueue` có contract rõ, test được độc lập.
- Hai RabbitMQ adapters tồn tại, import được khi optional dependency đã cài.
- Không message nào chứa raw frame hoặc object Python không JSON-safe.
- ACK/retry/reject/DLQ behavior được unit test; broker integration test là opt-in.
- Test suite hiện tại không regression.
- Tài liệu chỉ rõ RabbitMQ code đã sẵn sàng nhưng VLM runtime chưa được phép chọn
  Rabbit backend cho đến khi có `FrameStore` và transport-safe `VLMJob`.
