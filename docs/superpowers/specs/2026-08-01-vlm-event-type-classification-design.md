# VLM Event Type Classification Design

## Mục tiêu

Cho phép Qwen kết luận loại sự kiện cụ thể vì Motion/YOLO/router hiện chỉ đủ tạo
nghi vấn rộng. Candidate tiếp tục định hướng câu hỏi, nhưng không còn bị dùng làm
`event_type` cuối cùng khi response VLM hợp lệ.

## Output contract

Candidate-aware Qwen output có đúng ba field:

```json
{
  "decision": "yes",
  "event_type": "traffic_accident",
  "summary": "Một phương tiện va chạm, khiến người gần đó ngã xuống đường."
}
```

- `decision`: bắt buộc, một trong `yes`, `no`, `uncertain`.
- `event_type`: bắt buộc, một trong taxonomy cố định bên dưới.
- `summary`: bắt buộc, 1–2 câu tiếng Việt, mục tiêu không quá 40 từ.
- Không nhận additional properties.
- Không trả `evidence`, `risks`, `alert_level` hoặc `recommended_action`.

## Taxonomy cố định

```text
no_event
person_vehicle_interaction
traffic_accident
person_fall
fighting
fire_smoke
camera_tamper
unknown_event
```

Ý nghĩa:

- `no_event`: không có sự kiện cần cảnh báo.
- `person_vehicle_interaction`: có tương tác người–phương tiện nhưng chưa thấy
  tai nạn.
- `traffic_accident`: có bằng chứng va chạm/tai nạn giao thông.
- `person_fall`: có người ngã, không đủ bằng chứng kết luận tai nạn giao thông.
- `fighting`: có hành vi xô xát/đánh nhau.
- `fire_smoke`: có dấu hiệu cháy hoặc khói.
- `camera_tamper`: camera bị che, dịch chuyển hoặc phá hoại.
- `unknown_event`: có tín hiệu đáng chú ý nhưng không đủ để phân loại.

## Quy tắc nhất quán

| Decision | Event type hợp lệ |
|---|---|
| `no` | chỉ `no_event` |
| `uncertain` | chỉ `unknown_event` |
| `yes` | một trong sáu event cụ thể; không nhận `no_event` hoặc `unknown_event` |

Response có đủ ba field nhưng vi phạm bảng trên được xem là không hợp lệ. Backend
không tự sửa kết luận mâu thuẫn vì điều đó sẽ che giấu lỗi model.

## Prompt

Prompt gửi candidate type, evidence router, detection summary và ảnh trước/sau.
Candidate được mô tả là nghi vấn định hướng, không phải đáp án bắt buộc. Prompt
yêu cầu Qwen:

1. quan sát ảnh trước/sau;
2. chọn decision;
3. chọn đúng một event type trong taxonomy;
4. viết summary 1–2 câu, tối đa khoảng 40 từ;
5. không suy diễn tai nạn chỉ vì người và xe cùng xuất hiện.

## Backend mapping

Khi JSON hợp lệ:

- `VLMAnalysisTrace.event_type` lấy từ output Qwen;
- `ModelDecision.event_type` giữ nguyên giá trị này;
- `evidence=[]`, `risks=[]`;
- alert level vẫn được suy ra từ `decision + candidate.priority` trong giai đoạn
  này;
- chỉ valid `decision=yes` mới tạo `AlertEvent`.

Khi JSON sai, bị cắt, event type ngoài taxonomy hoặc decision/event type mâu
thuẫn:

- `raw_output_valid=false`;
- `decision=uncertain`;
- `event_type=unknown_event`;
- scene low, degraded;
- không tạo alert.

Không fallback event type về candidate type khi VLM response không hợp lệ, vì
candidate chỉ là nghi vấn và có thể quá rộng.

## Token budget và tương thích

Giữ `OLLAMA_NUM_PREDICT=128`. Ba field và summary tối đa khoảng 40 từ vẫn nằm
trong budget này. Non-candidate schema của ảnh tĩnh giữ nguyên.

Các contract `VLMAnalysisTrace`, `ModelDecision`, `AlertEvent` không đổi kiểu dữ
liệu. Chỉ nguồn của `event_type` thay từ candidate sang kết luận VLM hợp lệ.

## Kiểm thử và tiêu chí hoàn thành

- Schema Ollama có đúng `decision`, `event_type`, `summary` và enum taxonomy.
- `yes + traffic_accident`, `no + no_event`, `uncertain + unknown_event` hợp lệ.
- Các cặp mâu thuẫn như `no + traffic_accident` và `yes + no_event` bị từ chối.
- Event type ngoài taxonomy bị từ chối.
- Invalid response fallback `uncertain + unknown_event + low + degraded`.
- Summary/prompt không yêu cầu evidence/risks/action.
- Full regression pass; benchmark thực tế đo accuracy, truncated rate và latency.
