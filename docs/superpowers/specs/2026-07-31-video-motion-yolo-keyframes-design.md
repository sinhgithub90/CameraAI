# Thiết kế MVP pipeline phân tích video theo Motion → YOLO → VLM

## Phạm vi

Áp dụng cho luồng `MediaType.VIDEO` trong `SecurityAIPipeline`. Luồng ảnh giữ nguyên. Mục tiêu là giảm số lần chạy YOLO/VLM nhưng vẫn gửi VLM khi có thay đổi hình ảnh dù YOLO không nhận ra object quen thuộc.

## Kiến trúc

```text
VideoReader
    ↓
MotionDetector (khoảng 5 FPS)
    ↓ khi có thay đổi
YOLO + FireDetector (khoảng 2 FPS, tăng theo motion)
    ↓
CandidateSelector
    ↓
KeyframeSelector (4–8 frame / cửa sổ)
    ↓
VLMAnalyzer
    ↓
PipelineResult
```

Các stage được tách thành interface/module độc lập. Pipeline hiện tại vẫn chạy đồng bộ để dễ kiểm thử và không thay đổi API FastAPI. Thiết kế dữ liệu trung gian có `camera_id`, timestamp/frame index và window để sau này có thể đưa qua queue mà không phải viết lại detector.

## Xử lý video

1. Đọc video một lần, giới hạn kích thước frame và số frame tối đa như hiện tại.
2. Lấy mẫu Motion theo tốc độ cấu hình, mặc định tương đương 5 FPS.
3. Motion so sánh frame hiện tại với frame nền/lân cận, trả về `motion`, `changed_ratio`, vùng thay đổi và `motion_score`.
4. Nếu video không có motion đáng kể, bỏ qua YOLO, FireDetector và VLM; trả về kết quả LOW với `vlm.skipped=true`.
5. Với vùng/cửa sổ có motion, chạy YOLO và FireDetector theo nhịp thấp hơn, mặc định khoảng 2 FPS.
6. Tính điểm candidate dựa trên motion score, độ quan trọng của detection và vị trí thời gian trong cửa sổ.
7. Chọn tối đa 4–8 keyframe gồm frame đầu/cuối, trước motion, bắt đầu motion, motion cao nhất, detection cao nhất và sau motion. Không gửi trùng frame.
8. Gọi VLM một lần cho cả chuỗi keyframe. Nếu VLM hiện chỉ nhận một frame, mở rộng interface để nhận frame đại diện và metadata/keyframe theo cách tương thích ngược.

## Hợp đồng dữ liệu

Thêm các model nội bộ/framework-agnostic:

- `MotionResult`: `motion`, `changed_ratio`, `regions`, `score`.
- `VideoFrameObservation`: frame index, timestamp, frame, motion result, detections.
- `VideoAnalysisWindow`: danh sách observation và keyframes được chọn.

`PipelineResult` vẫn giữ các trường hiện có. Bổ sung metadata tùy chọn cho số frame đã đọc, số frame Motion, số frame YOLO, số keyframe và lý do skip để đánh giá hiệu quả MVP.

## Cấu hình

Cho phép cấu hình qua constructor/env nhưng có mặc định an toàn:

- Motion sample FPS: `5`.
- YOLO sample FPS: `2`.
- Motion threshold: giá trị đủ thấp để không bỏ sót thay đổi rõ ràng, nhưng có debounce để giảm nhiễu.
- Keyframe tối thiểu/tối đa: `4`/`8`.
- Cửa sổ phân tích: khoảng `5` giây.

Không thêm tracking, queue, batching hoặc thay model trong MVP.

## Xử lý lỗi

- Video không mở được hoặc không có frame: giữ nguyên `ValueError` hiện tại.
- Motion lỗi ở một frame: ghi log và tiếp tục với frame kế tiếp.
- YOLO/FireDetector lỗi: không làm hỏng toàn bộ cửa sổ; VLM vẫn có thể nhận keyframe nếu có motion.
- VLM lỗi: giữ cơ chế fallback/degraded hiện tại.
- Giải phóng `VideoCapture` và file tạm trong mọi trường hợp.

## Kiểm thử và tiêu chí chấp nhận

- Video tĩnh: không gọi YOLO/VLM, trả về LOW và `vlm.skipped=true`.
- Video có vùng thay đổi: Motion phát hiện được và VLM được gọi tối đa một lần cho cửa sổ.
- Motion có nhưng YOLO không có detection: VLM vẫn được gọi.
- Candidate selector chọn được các mốc trước/trong/sau thay đổi, không vượt quá 8 keyframe.
- Giữ tương thích các test ảnh hiện có.
- Có test đếm số lần gọi detector/VLM để xác nhận giảm xử lý so với mọi frame.

## Định hướng mở rộng

Các stage không phụ thuộc FastAPI và giao tiếp bằng data contract rõ ràng. Vì vậy có thể chuyển orchestration đồng bộ thành các worker/queue ở giai đoạn sau mà không thay đổi logic Motion, YOLO, selector hoặc VLM.
