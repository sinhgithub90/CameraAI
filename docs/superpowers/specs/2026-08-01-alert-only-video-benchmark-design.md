# Alert-only Video Benchmark CLI Design

## Mục tiêu

Tạo CLI gọi pipeline video async hiện tại cho một video hoặc toàn bộ thư mục,
phục vụ kiểm tra nhanh mà không thay đổi runtime API. CLI chưa tự khởi động
server và không được tự chạy khi cài đặt.

## Giao diện dòng lệnh

CLI dùng `scripts/benchmark_pipeline.py` và hỗ trợ đúng một trong hai input:

```powershell
python -m scripts.benchmark_pipeline `
  --input-dir "D:\CongViec\CameraAI\CameraAI\videos" `
  --output-dir "D:\CongViec\CameraAI\CameraAI\runs\alerts" `
  --base-url "http://127.0.0.1:8000"
```

```powershell
python -m scripts.benchmark_pipeline `
  --input-file "D:\CongViec\CameraAI\CameraAI\videos\RoadAccidents005_x264.mp4" `
  --output-dir "D:\CongViec\CameraAI\CameraAI\runs\alerts" `
  --base-url "http://127.0.0.1:8000"
```

`--input-dir` và `--input-file` loại trừ nhau. Chế độ thư mục nhận `.mp4`,
`.avi`, `.mov`, `.mkv`, sắp xếp theo tên và xử lý tuần tự để không tranh GPU.
Các tùy chọn vận hành gồm `--poll-interval` và `--timeout`.

## Luồng xử lý

Mỗi video được gửi tới `POST /async/analyze/video`. CLI polling
`GET /analyses/{analysis_id}` cho đến `completed` hoặc `failed`, sau đó lọc
`VideoWindowResult`:

- `low`: bỏ qua;
- `medium`: cảnh báo cam;
- `high`: cảnh báo đỏ.

Nếu video không có window `medium/high`, CLI không tạo JSON. Nếu có cảnh báo,
CLI tạo đúng một file `<video-stem>.json` bằng ghi nguyên tử. Lỗi của một video
được in ra terminal và không ngăn chế độ thư mục chạy video tiếp theo; lỗi
không được giả thành cảnh báo.

## Cấu trúc JSON

```json
{
  "video": "RoadAccidents005_x264.mp4",
  "camera_id": "RoadAccidents005_x264",
  "analysis_id": "...",
  "status": "completed",
  "alert_summary": {
    "orange": 1,
    "red": 1,
    "highest_level": "high"
  },
  "alerts": [
    {
      "window_index": 1,
      "start_seconds": 5.0,
      "end_seconds": 10.0,
      "alert_level": "high",
      "summary": "...",
      "risks": [],
      "recommended_action": "...",
      "detections": [],
      "qwen_input": {},
      "timing": {}
    }
  ]
}
```

Chỉ các window cam/đỏ xuất hiện trong `alerts`. Không nhúng frame, ảnh base64
hoặc raw video vào JSON.

## Tương thích

Giữ nguyên chế độ manifest hiện có để không phá benchmark runner trước đó.
Các input mới dùng chung HTTP client/polling, nhưng phần lọc và ghi alert JSON
được tách thành hàm thuần để kiểm tra nhanh. Không thay đổi FastAPI endpoint,
pipeline, queue hoặc model configuration.

## Kiểm chứng tối thiểu

- Một video chỉ có `low` không tạo file.
- Một video có `medium/high` tạo đúng một JSON và chỉ chứa window cảnh báo.
- `--input-file` chỉ gửi một video.
- `--input-dir` xử lý đúng các extension hỗ trợ theo thứ tự tên.
- Lỗi một video không dừng batch.
