# Cấu hình input YOLO26n và Qwen-VL 4B

## Mục tiêu

Luồng upload ảnh/video hiện có tiếp tục được sử dụng, nhưng cấu hình mặc định chuyển sang YOLO26n cho object detection và `qwen3-vl:4b-instruct-q4_K_M` cho phân tích VLM qua Ollama.

## Quyết định

- YOLO object detector mặc định dùng `yolo26n.pt`; Ultralytics tự tải weights ở lần chạy đầu.
- FireDetector vẫn chạy riêng để giữ khả năng phát hiện fire/smoke.
- VLM mặc định dùng model Ollama `qwen3-vl:4b-instruct-q4_K_M`, model đã có trong máy.
- Người dùng có thể ghi đè bằng `YOLO_WEIGHTS`, `OLLAMA_MODEL`, `OLLAMA_BASE_URL` và `FIRE_MODEL`.
- API upload vẫn dùng `POST /analyze/image` và `POST /analyze/video`; không thay đổi response contract.

## Kiểm thử

- Test default YOLO weights và biến môi trường override.
- Test default Qwen model và biến môi trường override.
- Giữ toàn bộ test pipeline hiện có.
