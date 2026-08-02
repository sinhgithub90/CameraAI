# Multi-Video Upload — Design Spec

> **Ngày:** 2026-08-01
> **Phạm vi:** Thêm một entry nhận nhiều file video và đưa từng video vào pipeline async hiện tại.

## 1. Mục tiêu

Thêm endpoint `POST /async/analyze/videos` nhận từ 1 đến 20 video trong một
multipart request. Mỗi video tạo một `VideoAnalysis` độc lập, chạy qua
`_produce_video_windows()`, `VLMQueue` và `VLMWorker` hiện tại. Endpoint trả về
ngay sau khi tiếp nhận và lên lịch tất cả video; client tiếp tục poll từng
`analysis_id` bằng API hiện có.

## 2. Ngoài phạm vi

- Không bật RabbitMQ.
- Không thêm nhiều VLM worker hoặc thay đổi scheduling/priority.
- Không thêm BatchStore hoặc endpoint lấy trạng thái batch.
- Không thay đổi motion, YOLO, candidate routing, Qwen hay window 5 giây.
- Không thiết kế live camera stream.
- Không thay đổi replay pacing hiện tại của video.

## 3. API contract

### Request

```http
POST /async/analyze/videos
Content-Type: multipart/form-data

files=<video-1>
files=<video-2>
...
```

Validation:

- Ít nhất 1 file, tối đa 20 file.
- File rỗng làm toàn bộ request trả HTTP 400.
- File được stage xuống temporary path theo từng chunk; không giữ đồng thời toàn
  bộ nội dung của 20 video trong RAM.
- Nếu staging bất kỳ file nào thất bại, xóa mọi temporary file đã tạo và không
  khởi chạy producer nào.

### Response

```json
{
  "batch_id": "uuid",
  "items": [
    {
      "filename": "camera-01.mp4",
      "camera_id": "camera-01",
      "analysis_id": "uuid"
    }
  ]
}
```

`camera_id` được tạo từ filename stem sau khi sanitize. Filename trùng nhau được
gắn suffix theo thứ tự (`camera-01`, `camera-01-2`, ...). Filename trống dùng
`camera-<index>`.

## 4. Data flow

```text
multipart files
    ↓ stage từng file xuống temp path
validate toàn batch
    ↓
tạo batch_id
    ↓ mỗi file
tạo analysis_id + VideoAnalysis
    ↓
asyncio.create_task(_produce_video_windows(...))
    ↓
RawVideoWindow → VLMQueue → VLMWorker → AnalysisStore
```

Mỗi producer được bọc bằng cleanup `finally`, nên temporary file bị xóa khi video
hoàn tất, decode lỗi hoặc task bị cancel. Lỗi của một producer chỉ cập nhật
analysis tương ứng thành `failed`, không cancel các producer còn lại.

## 5. Code boundaries

- Pydantic response models nằm trong `apps/api/main.py` vì đây là HTTP adapter
  contract, không phải domain event.
- Helper stage upload và derive camera ID nằm trong `apps/api/main.py`; không đưa
  logic HTTP vào core pipeline.
- `_produce_video_windows()` nhận thêm optional cleanup path và xóa nó trong
  `finally`.
- Endpoint một-video hiện tại giữ nguyên contract và behavior.

## 6. Error handling

- 0 file hoặc trên 20 file: HTTP 400.
- File rỗng/staging lỗi: HTTP 400, cleanup toàn bộ file đã stage.
- Video không decode được sau khi response đã trả: analysis tương ứng chuyển
  `failed` qua error handling sẵn có của producer.
- Failure của video A không thay đổi trạng thái video B.

## 7. Tests

- Upload 3 video trả 3 item với analysis ID khác nhau.
- Camera ID được derive đúng và xử lý filename trùng.
- Từ chối batch rỗng và batch trên 20 file.
- File rỗng cleanup mọi staged file và không tạo analysis.
- Mỗi producer nhận đúng path/camera/analysis mapping.
- Producer thành công, lỗi và cancel đều cleanup temporary file.
- Endpoint `/async/analyze/video` hiện tại không regression.

## 8. Tiêu chí hoàn thành

- Một request nhận và lên lịch được tối đa 20 video.
- Từng video xuất hiện độc lập qua `GET /analyses/{analysis_id}`.
- Không giữ toàn bộ batch video trong RAM.
- Không bật hoặc phụ thuộc RabbitMQ runtime.
- Toàn bộ test suite hiện tại và test mới pass.
