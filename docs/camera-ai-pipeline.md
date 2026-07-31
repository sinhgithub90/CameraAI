# Camera AI Pipeline

Tài liệu này mô tả ngắn gọn pipeline đang được triển khai trong dự án. Đây là
trạng thái hiện tại của mã nguồn, không bao gồm các phương án tối ưu Qwen 2B
chưa triển khai.

## 1. Mục tiêu

Pipeline dùng camera/video để:

- phát hiện chuyển động;
- nhận diện đối tượng bằng YOLO26n;
- chọn các khung hình đáng chú ý;
- dùng Qwen-VL đánh giá ngữ cảnh và mức cảnh báo;
- trả về kết quả có cấu trúc để giao diện hoặc worker sử dụng.

Fire heuristic đã được loại khỏi runtime. Nếu có khói hoặc lửa, Qwen nhận biết
trực tiếp từ keyframe.

## 2. Luồng xử lý video

```text
Video
  │
  ├─ Chia thành cửa sổ 5 giây
  │
  ├─ Motion lấy mẫu 5 FPS
  │     └─ Không có chuyển động → bỏ qua YOLO và Qwen
  │
  ├─ Có chuyển động
  │     └─ YOLO26n chạy tối đa 2 FPS
  │
  ├─ Chọn tối đa 2 keyframe đáng chú ý
  │     ├─ Frame có điểm Motion/YOLO cao nhất
  │     └─ Frame tốt tiếp theo, cách frame đầu ít nhất 1 giây
  │
  ├─ Gửi 2 frame và nhãn YOLO vào Qwen-VL
  │
  └─ Trả về cảnh báo, detection, timing và ảnh minh họa
```

Pipeline duyệt đủ vị trí frame để giữ đúng timestamp, nhưng chỉ giải mã ảnh
BGR tại các mốc Motion cần sử dụng. Ví dụ video 30 FPS có 150 frame trong 5
giây nhưng chỉ khoảng 25 frame được lấy ra để kiểm tra Motion.

## 3. Khi nào Qwen được gọi?

Qwen không được gọi cố định sau mỗi 5 giây.

| Trạng thái cửa sổ | YOLO | Qwen |
|---|---:|---:|
| Không có chuyển động | Không chạy | Không chạy |
| Có chuyển động | Chạy tối đa 2 FPS | Gọi một lần |
| Có chuyển động nhưng YOLO không tìm thấy đối tượng | Có chạy | Vẫn gọi một lần |

Việc vẫn gọi Qwen khi YOLO không có detection giúp giảm nguy cơ bỏ sót khói,
lửa, té ngã, vật cản hoặc sự kiện không thuộc tập nhãn COCO.

Với camera luôn có chuyển động, Qwen có thể bị gọi ở mỗi cửa sổ 5 giây. Khi
triển khai camera liên tục nên bổ sung cooldown/dedup để không phân tích lặp lại
cùng một cảnh báo.

## 4. Vai trò từng tầng

### Motion

- Tần suất mặc định: 5 FPS.
- Là tầng rẻ nhất và quyết định cửa sổ có cần xử lý tiếp hay không.
- Video tĩnh dừng tại đây.

### YOLO26n

- Model: `yolo26n.pt`.
- Chỉ chạy khi có Motion, tối đa 2 FPS.
- Cung cấp cho Qwen một dòng mỗi nhãn gồm số lượng và confidence cao nhất.
- Bounding box và các detection đầy đủ vẫn có trong output pipeline, nhưng
  không được lặp trong prompt Qwen.

### Keyframe selector

- Mặc định chọn tối đa hai frame.
- Ưu tiên tín hiệu Motion/YOLO thay vì lấy máy móc frame đầu và cuối.
- Hai frame được tách nhau ít nhất một giây khi có đủ ứng viên.

### Qwen-VL

- Model hiện tại: `qwen3-vl:4b-instruct-q4_K_M`.
- Context: 4096.
- Nhận hai ảnh keyframe riêng theo thứ tự thời gian.
- Giới hạn output: 96 token.
- Giữ model trong Ollama: 10 phút.
- Dùng JSON Schema để trả về alert level, summary, risks và recommended action.
- `observations` của API được suy ra từ summary để giữ tương thích.

## 5. Luồng xử lý ảnh tĩnh

```text
Ảnh → YOLO26n → VLMGate
                 ├─ Có detection → Qwen-VL
                 └─ Không có detection → bỏ qua Qwen
```

Ở policy `always`, ảnh tĩnh vẫn được gửi tới Qwen dù YOLO không có detection.

## 6. Cấu hình mặc định

| Tham số | Giá trị |
|---|---:|
| Độ dài cửa sổ | 5 giây |
| Motion sampling | 5 FPS |
| YOLO sampling | 2 FPS |
| Keyframe gửi Qwen | 2 |
| Số cửa sổ video test | 1 |
| YOLO model | `yolo26n.pt` |
| Qwen model | `qwen3-vl:4b-instruct-q4_K_M` |
| Ollama context | 4096 |
| Ollama output limit | 96 token |
| Ollama keep-alive | 10 phút |

Mặc định upload video chỉ đọc cửa sổ 5 giây đầu tiên
(`max_video_windows=1`). Đặt `max_video_windows=None` khi khởi tạo pipeline để
xử lý toàn bộ video theo từng cửa sổ 5 giây.

## 7. Output chính

Kết quả `PipelineResult` gồm:

- danh sách detection của YOLO;
- kết quả phân tích và mức cảnh báo của Qwen;
- ảnh đại diện có bounding box;
- số frame đã đọc, số cửa sổ và số lần gọi Qwen;
- chỉ số frame được gửi vào Qwen;
- thời gian Motion, detector, Qwen và toàn pipeline.

Ví dụ log:

```text
Read: 150 frames | windows: 1 | Qwen calls: 1 | total: ...ms
overhead | open ...ms, grab ...ms, retrieve ...ms, resize ...ms, window ...ms, untracked ...ms
window 0 [0-5s] | Qwen frames: 24, 120 | labels: car, person |
motion ...ms, detector ...ms, Qwen ...ms
```

Các trường overhead tách phần thời gian ngoài Motion, YOLO và Qwen. `window`
bao gồm chọn keyframe và dựng kết quả cửa sổ; `untracked` là phần dư để tổng
các timer luôn khớp với `total_ms`.

## 8. Chạy thử

```powershell
$env:OLLAMA_NUM_CTX="4096"
$env:OLLAMA_NUM_PREDICT="96"
$env:OLLAMA_KEEP_ALIVE="10m"
python -m uvicorn apps.api.main:app --reload
```

Sau khi upload video, kiểm tra Ollama:

```powershell
ollama ps
```

Kỳ vọng model chạy với `100% GPU`, context `4096`. Cold-run đầu tiên chậm hơn
do nạp model; các lần tiếp theo trong thời gian keep-alive là warm-run.
