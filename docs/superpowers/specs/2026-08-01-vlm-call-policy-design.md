# VLM Call Policy Design

## Mục tiêu

Giảm số lần gọi Qwen trong pipeline video async mà không bỏ qua cửa sổ có tín
hiệu đáng chú ý. Mỗi cửa sổ nguồn dài 5 giây phải hoàn tất xử lý trong không quá
5 giây trên môi trường benchmark mục tiêu. Mọi cửa sổ, kể cả cửa sổ xanh hoặc
bỏ qua VLM, vẫn phải xuất hiện trong JSON theo video.

## Phạm vi

Thay đổi chỉ áp dụng cho đường video async và benchmark tương ứng. Phiên bản đầu
không thêm tracking, zone/line, model 2B cascade, queue mới hoặc gọi VLM riêng cho
từng candidate. Qwen 4B vẫn là VLM mặc định khi policy quyết định cần xác minh.

## Luồng xử lý

```text
5-second window
  -> Motion + YOLO + optional specialized detector
  -> VideoWindowObservation
  -> Event Router -> zero or more CandidateEvent
  -> VLMCallPolicy
       -> skip: static or unusable window
       -> call: one primary candidate, one Qwen call
  -> ProcessedVideoWindow
  -> AnalysisStore / per-video benchmark JSON
```

`VLMCallPolicy` là logic domain độc lập với queue, HTTP và persistence. Policy
nhận observation, danh sách candidate, số frame được chọn và trả một quyết định
có thể tuần tự hóa:

```json
{
  "call_vlm": false,
  "reason": "static_window",
  "priority": "low",
  "candidate_id": null
}
```

## Chính sách phiên bản đầu

| Tín hiệu | Candidate | Quyết định |
|---|---|---|
| Không motion, không detection, không tín hiệu chuyên biệt | Không có | Bỏ Qwen: `static_window` |
| Không có keyframe hợp lệ | Bất kỳ | Bỏ Qwen: `no_usable_frames`, kết quả degraded |
| Có motion nhưng YOLO không phát hiện | `unknown_motion` | Gọi Qwen |
| Có người hoặc xe | Candidate tương ứng | Gọi Qwen |
| Người gần xe | `possible_person_vehicle_interaction` | Gọi Qwen |
| Nhiều người và motion cao | `multi_person_high_motion` | Gọi Qwen |
| Fire/smoke chưa đạt temporal confirmation | Chưa có fire candidate | Không tự tạo cảnh báo cháy |
| Fire/smoke đã temporal-confirmed | `possible_fire_visual_change` | Gọi Qwen với candidate cháy |

Policy bảo thủ: chỉ cửa sổ hoàn toàn tĩnh bị gate theo nội dung. Không bỏ Qwen
cho `person_only_activity`, `vehicle_only_activity` hoặc `unknown_motion` trước
khi benchmark có dữ liệu chứng minh an toàn.

## Candidate và bbox

Router có thể tạo nhiều candidate nhưng chỉ chọn một primary candidate và gọi
Qwen đúng một lần cho mỗi window. Prompt giới hạn VLM vào candidate primary;
không thực hiện một request cho mỗi candidate.

Bbox tiếp tục được giữ trong observation nội bộ để tính proximity, chọn frame,
tracking và zone/rule về sau. Prompt Qwen chỉ nhận nhãn, số lượng, confidence và
bằng chứng candidate; không nhận tọa độ bbox thô.

YOLO26n COCO không được coi là detector cháy/khói. Candidate cháy chỉ được sinh
từ specialized detector đã bật và đã đạt temporal confirmation.

## Kết quả khi bỏ qua VLM

`ProcessedVideoWindow` lưu quyết định policy. Một cửa sổ static bị bỏ qua có:

- scene mức `low`, không degraded;
- VLM status `skipped`;
- `qwen_ms = 0`;
- không có `ModelDecision` và `AlertEvent`;
- trace không có prompt/raw output;
- lý do `static_window` được lưu trong metadata và benchmark JSON.

Cửa sổ không có frame hợp lệ cũng có VLM status `skipped`, nhưng scene phải
`degraded` và reason là `no_usable_frames`.

## Ngân sách hiệu năng

SLO là thời gian xử lý một window, tính từ lúc worker bắt đầu Motion cho tới khi
có `ProcessedVideoWindow`, không vượt quá 5.0 giây tại percentile p95 trên bộ
benchmark mục tiêu. Thời gian chờ queue được đo riêng và không nằm trong SLO xử
lý, nhưng wall-clock và queue wait vẫn phải được báo cáo để phát hiện backlog.

Mỗi stage tiếp tục có timing riêng: Motion, detector, keyframe và Qwen. Benchmark
bổ sung:

- `vlm_called_windows`;
- `vlm_skipped_windows`;
- `vlm_call_rate`;
- số window vượt 5 giây và `processing_p95_ms`;
- thống kê theo candidate type và policy reason.

Không thể bảo đảm tuyệt đối Qwen luôn trả dưới 5 giây chỉ bằng gate. Nếu các
window phải gọi Qwen vẫn vượt SLO, bước tối ưu kế tiếp phải được quyết định bằng
benchmark: giảm kích thước ảnh/composite, timeout có kiểm soát, hoặc model nhanh
hơn. Phiên bản policy này không tự đổi model.

## Lỗi và tương thích

- Policy phải deterministic và không ném lỗi với danh sách candidate rỗng.
- Lỗi VLM sau khi policy chọn `call` giữ fallback/degraded hiện tại.
- API hiện tại và các field cũ của `VideoWindowResult` không bị xóa.
- Field policy và thống kê mới là additive.
- JSON theo video vẫn được ghi cho mọi window và cho video thất bại.

## Kiểm thử và tiêu chí hoàn thành

Unit test xác nhận static window không gọi fake VLM, còn unknown motion, người,
xe, tương tác người-xe và fire-confirmed vẫn gọi đúng một lần. Test persistence
xác nhận status/reason được giữ. Test benchmark xác nhận số call/skip và SLO được
tổng hợp đúng.

Hoàn thành khi toàn bộ test hiện có vẫn pass, test mới pass, benchmark CLI vẫn
tạo một JSON cho mỗi video, và report thể hiện rõ cửa sổ nào vượt ngân sách 5
giây. Việc chứng minh p95 thực tế dưới 5 giây cần một benchmark runtime riêng với
API và Ollama đang chạy; implementation không được tuyên bố đạt SLO nếu chưa có
số đo đó.
