# Candidate-aware VLM Output Design

## Mục tiêu

Tăng độ ổn định JSON và làm kết quả Qwen bám sát nghi vấn của từng window mà
không đổi pipeline 4B, hai keyframe composite hoặc số lần gọi VLM.

## Thay đổi

- Tăng `OLLAMA_NUM_PREDICT` mặc định từ 192 lên 256.
- Event Router và chọn primary candidate chạy trước VLM.
- VLM nhận candidate type/evidence để tạo câu hỏi xác minh ngắn, yêu cầu đúng
  `yes | no | uncertain` cùng `event_type`, `evidence`, `alert_level`,
  `summary`, `risks`, `recommended_action`.
- Adapter trả một trace typed gồm prompt thực tế, raw output, schema validity
  và SceneAnalysis/ModelDecision đã parse. Trace thuộc từng request, không dùng
  mutable `last_trace` dùng chung giữa các worker.
- Parser chỉ phục hồi JSON cắt khi đọc được các trường độc lập an toàn. JSON
  thiếu đóng ngoặc hoặc thiếu trường bắt buộc được đánh dấu invalid/degraded;
  decision mặc định `uncertain`, không tạo AlertEvent.
- Artifact lưu prompt/raw output thật từ trace thay vì chuỗi mô phỏng.
- JSON benchmark mỗi window bổ sung candidate, decision và
  `raw_output_valid`; vẫn giữ toàn bộ window xanh/cam/đỏ.

## Luồng

```text
Motion/YOLO/keyframes
  → VideoWindowObservation
  → Event Router
  → primary CandidateEvent
  → candidate-aware Qwen call
  → VLMAnalysisTrace
  → ModelDecision
  → AlertEvent policy
  → AnalysisStore/artifact/benchmark JSON
```

Window không có candidate vẫn gọi Qwen theo hành vi async hiện tại với prompt
baseline ngắn. Không có candidate thì không tạo ModelDecision/AlertEvent.

## Tương thích và lỗi

`VLMAnalyzer.analyze()` và `analyze_sequence()` tiếp tục trả `SceneAnalysis`
cho caller cũ. Một interface bổ sung trả trace được dùng bởi video processor;
mock/custom analyzer không hỗ trợ trace được bọc thành trace degraded-compatible
mà không làm hỏng pipeline. Ollama unreachable giữ fallback hiện tại.

## Kiểm chứng tối thiểu

- Payload mặc định dùng `num_predict=256`.
- Candidate prompt chứa candidate type và chỉ dẫn decision enum.
- Raw JSON hợp lệ tạo valid trace và decision tương ứng.
- JSON cắt tạo `raw_output_valid=false`, `uncertain`, không alert.
- Artifact ghi đúng prompt/raw response trace.
- Benchmark JSON chứa candidate/decision/validity cho mỗi window.
