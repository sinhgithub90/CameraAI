import cv2
import torch
from collections import deque
# Import thêm AutoFeatureExtractor đề phòng phiên bản cũ
from transformers import AutoImageProcessor, AutoModelForVideoClassification, AutoFeatureExtractor

# 1. Cấu hình thiết bị chạy
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
print(f"Đang chạy trên thiết bị: {device}")

# 2. Tải Processor và Model
model_name = "jatinmehra/Accident-Detection-using-Dashcam"
print("Đang tải model và processor...")

# SỬA LỖI TẠI ĐÂY:
try:
    # Thử cách 1: Dùng AutoFeatureExtractor nếu tác giả dùng chuẩn cấu hình cũ
    processor = AutoFeatureExtractor.from_pretrained(model_name)
except Exception:
    # Cách 2 (Mẹo bypass): Nếu repo thiếu file cấu hình xử lý ảnh, 
    # ta mượn tạm bộ xử lý của mô hình gốc VideoMAE (bản chất xử lý khung hình y hệt nhau)
    print("Phát hiện Repo thiếu file cấu hình ảnh! Đang tải bộ xử lý thay thế từ mô hình gốc VideoMAE...")
    processor = AutoImageProcessor.from_pretrained("MCG-NJU/videomae-base")

# Load mô hình nhận diện tai nạn bình thường
model = AutoModelForVideoClassification.from_pretrained(model_name).to(device)
model.eval()

print("Model và Processor đã được nạp thành công!")

# 3. Đường dẫn tới video local trên máy của bạn
video_path = "C:\\Users\\LXT\\Downloads\\2.mp4" 
cap = cv2.VideoCapture(video_path)

if not cap.isOpened():
    print(f"Lỗi: Không thể mở file video tại {video_path}")
    exit()

# Lấy số khung hình yêu cầu của mô hình (Mặc định thường là 16 frames liên tiếp)
# Nếu config không ghi rõ, ta sẽ sử dụng mặc định là 16 khung hình.
num_frames_required = getattr(model.config, "num_frames", 16) 
print(f"Mô hình yêu cầu tối thiểu: {num_frames_required} frames liên tiếp để dự đoán.")

# Sử dụng deque (hàng đợi buffer) để lưu trữ các khung hình liên tiếp theo dạng cuộn
frame_buffer = deque(maxlen=num_frames_required)
current_prediction = "Đang thu thập dữ liệu..."

print("Bắt đầu xử lý video... Nhấn 'q' để THOÁT.")

while cap.isOpened():
    success, frame = cap.read()
    if not success:
        print("Đã chạy hết video hoặc lỗi file.")
        break

    # OpenCV đọc ảnh dạng BGR, nhưng Hugging Face yêu cầu định dạng chuẩn màu RGB
    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    
    # Đẩy frame hiện tại vào bộ đệm buffer
    frame_buffer.append(rgb_frame)

    # Khi bộ đệm tích lũy ĐỦ số lượng frame (ví dụ: đủ 16 frames)
    if len(frame_buffer) == num_frames_required:
        # Chuyển list các frame qua processor để chuẩn hóa kích thước, normalized...
        # Đầu vào truyền dạng list các numpy array [frame1, frame2, ...]
        inputs = processor(list(frame_buffer), return_tensors="pt").to(device)
        
        # Tiến hành dự đoán không tính gradient để tiết kiệm RAM/VRAM
        with torch.no_grad():
            outputs = model(**inputs)
            logits = outputs.logits
            
            # Lấy index của class có xác suất cao nhất
            predicted_class_idx = logits.argmax(-1).item()
            
            # Khớp index với nhãn (Label) thực tế được khai báo trong model config
            current_prediction = model.config.id2label[predicted_class_idx]

    # Vẽ chữ hiển thị kết quả AI dự đoán lên góc trên màn hình video
    # Nếu phát hiện tai nạn (tùy thuộc vào nhãn của model, thường chứa chữ 'Accident')
    color = (0, 0, 255) if "accident" in current_prediction.lower() else (0, 255, 0)
    
    cv2.putText(
        frame, 
        f"AI Predict: {current_prediction}", 
        (30, 50), 
        cv2.FONT_HERSHEY_SIMPLEX, 
        1, 
        color, 
        2, 
        cv2.LINE_AA
    )

    # Hiển thị video ra màn hình
    cv2.imshow("Accident Detection Test", frame)

    # Nhấn q để thoát
    if cv2.waitKey(20) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
print("Chương trình kết thúc thành công.")