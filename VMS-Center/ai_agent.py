import cv2
import requests

SERVER_API = "http://127.0.0.1:8000"

# Bước A: Gõ cửa Máy chủ trung tâm để xin Token xác thực
print("[1] Đang kết nối xác thực với VMS Trung tâm...")
auth_resp = requests.post(f"{SERVER_API}/api/auth/token?user=ai_system&text_pass=secure_pass_2026")
token = auth_resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# Bước B: Gọi API lấy luồng xử lý của Camera 1
camera_target = "cam_huyen_01"
print(f"[2] Xin cấp quyền truy cập luồng dữ liệu {camera_target}...")
stream_resp = requests.get(f"{SERVER_API}/api/vms/stream/{camera_target}", headers=headers)
data = stream_resp.json()

print(f"-> Phân quyền thành công!")
print(f"-> Tên camera: {data['camera_name']} | Thuộc vùng mạng: {data['vlan_zone']}")
print(f"-> Link luồng nhận diện: {data['stream_link']}")

# Bước C: Đưa luồng video thu được vào OpenCV để xử lý AI
cap = cv2.VideoCapture(data['stream_link'])

print("[3] Đang khởi chạy luồng hình ảnh camera...")
while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break
        
    # ─── ĐƯA FRAME NÀY VÀO TRỤC XỬ LÝ MODEL AI (YOLO / CNN) CỦA BẠN ───
    # Ví dụ: kết quả nhận diện = model(frame)
    
    cv2.imshow(f"AI Agent Processing - {data['camera_name']}", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()