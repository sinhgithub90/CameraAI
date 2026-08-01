# Compact Candidate VLM Output Design

## Mục tiêu

Rút ngắn response của Qwen khi xác minh một `CandidateEvent`, giảm JSON bị cắt
giữa chừng và giảm lượng token sinh ra. Candidate-aware output chỉ còn kết quả
xác minh và một câu mô tả ngắn.

## Phạm vi

Thay đổi chỉ áp dụng khi router đã truyền candidate vào
`OllamaQwenAnalyzer.analyze_with_trace()`. Luồng phân tích không có candidate giữ
schema `SceneAnalysis` hiện tại để bảo toàn tương thích ảnh tĩnh và các caller
cũ. Không thay model, kích thước ảnh, router hoặc queue trong đợt này.

## Schema Qwen mới

Qwen chỉ phải trả:

```json
{
  "decision": "yes",
  "summary": "Người có tương tác rõ với phương tiện."
}
```

Quy tắc:

- `decision` bắt buộc và chỉ nhận `yes`, `no`, `uncertain`.
- `summary` bắt buộc, đúng một câu ngắn, mục tiêu không quá 20 từ.
- JSON không nhận field ngoài schema.

Các field bị bỏ khỏi candidate output:

- `event_type`;
- `evidence`;
- `alert_level`;
- `risks`;
- `recommended_action`.

`decision` phải được giữ vì đây là kết quả xác minh candidate. Nếu bỏ field này,
backend không phân biệt được `no` và `uncertain`.

## Suy diễn ở backend

Sau khi parse response hợp lệ, backend tạo trace/domain result như sau:

```text
event_type        = candidate.candidate_type
evidence          = []
risks             = []
recommended_action = mapping cố định theo candidate type và decision
```

Mức hiển thị được suy ra thay vì yêu cầu Qwen tự chọn:

| Decision | Candidate priority | Alert level |
|---|---|---|
| `no` | bất kỳ | `low` |
| `uncertain` | bất kỳ | `low` |
| `yes` | `low` | `low` |
| `yes` | `medium` | `medium` |
| `yes` | `high` hoặc `critical` | `high` |

Chỉ `yes` với JSON hợp lệ mới có thể tạo `AlertEvent`, giữ nguyên domain policy
hiện tại. `uncertain` hợp lệ không bị xem là lỗi parse và không tự tạo cảnh báo
cam. JSON sai hoặc bị cắt vẫn tạo trace `raw_output_valid=false`, decision
`uncertain`, scene `degraded=true` và alert level `low`.

Recommended action ban đầu dùng mapping tối thiểu:

- `yes`: `Kiểm tra sự kiện trên camera.`
- `no`: `Tiếp tục giám sát.`
- `uncertain`: `Kiểm tra lại hình ảnh.`

## Token budget

Giảm `OLLAMA_NUM_PREDICT` mặc định từ 256 xuống 128 cho cả cấu hình mặc định và
tài liệu môi trường. Schema mới chỉ có hai field nên 128 token đủ cho một JSON
ngắn, đồng thời để lại biên an toàn cho tiếng Việt. Giá trị vẫn có thể override
qua environment variable.

## Persistence và benchmark

`VLMAnalysisTrace` và `ModelDecision` giữ contract hiện tại để không phá API.
Các field không còn do Qwen sinh được backend điền deterministically:

- trace `event_type` lấy từ candidate;
- trace/model evidence là mảng rỗng;
- scene risks là mảng rỗng;
- scene alert/action lấy từ mapping trên.

Benchmark tiếp tục lưu `decision`, `raw_output_valid`, summary và candidate.
Không cần lưu các field Qwen đã bị loại bỏ như một output giả lập dài dòng.

## Kiểm thử và tiêu chí hoàn thành

- Ollama request dùng candidate schema chỉ gồm `decision` và `summary`.
- Prompt yêu cầu một câu ngắn và không yêu cầu evidence/risks/action/event type.
- `yes`, `no`, `uncertain` được ánh xạ đúng sang scene/domain result.
- `uncertain` hợp lệ có alert level low và không tạo alert.
- JSON bị cắt vẫn degraded/uncertain/low.
- Mặc định `num_predict=128` và environment override vẫn hoạt động.
- Full regression pass; benchmark thực tế mới quyết định mức giảm latency và tỷ
  lệ truncated response.
