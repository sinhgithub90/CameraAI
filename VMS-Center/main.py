from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
import requests
from datetime import datetime, timedelta

app = FastAPI(title="VMS Central API Gateway", version="1.0")
security = HTTPBearer()

SECRET_KEY = "CONG_NGHE_AI_QUY_NHON_GIA_LAI"
GO2RTC_API = "http://127.0.0.1:1984/api"

# Giả lập cơ sở dữ liệu 200 Camera quy hoạch theo phân vùng
CAMERA_DB = {
    "cam_huyen_01": {"name": "Camera Ngã tư Huyện 1", "vlan": "VLAN_10", "status": "active"},
    "cam_huyen_02": {"name": "Camera Cổng Ủy Ban Huyện 2", "vlan": "VLAN_10", "status": "active"}
}

# 1. API Đăng nhập cấp Token cho Model AI / Máy trạm giám sát
@app.post("/api/auth/token")
def login(user: str, text_pass: str):
    if user == "ai_system" and text_pass == "secure_pass_2026": # Đổi thông tin theo ý bạn
        payload = {
            "sub": user,
            "exp": datetime.utcnow() + timedelta(hours=8),
            "role": "ai_reader"
        }
        token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
        return {"access_token": token, "token_type": "bearer"}
    raise HTTPException(status_code=401, detail="Sai tài khoản hoặc mật khẩu")

# Hàm mã hóa kiểm tra Token hợp lệ
def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Mã xác thực Token không hợp lệ hoặc hết hạn")

# 2. API gọi điều phối cấp luồng WebRTC an toàn
@app.get("/api/vms/stream/{camera_id}")
def get_camera_stream(camera_id: str, user_info: dict = Depends(verify_token)):
    # Kiểm tra xem camera_id có tồn tại trong hệ thống quản lý không
    if camera_id not in CAMERA_DB:
        raise HTTPException(status_code=404, detail="Camera không nằm trong danh sách quy hoạch")
        
    # Gọi sang trục Go2RTC để tạo/lấy cấu trúc liên kết luồng
    try:
        # Trả về link giao thức WHEP/WebRTC chuẩn cho Client hoặc OpenCV đọc luồng siêu tốc
        webrtc_url = f"http://127.0.0.1:1984/api/stream.mp4?src={camera_id}"
        return {
            "camera_name": CAMERA_DB[camera_id]["name"],
            "vlan_zone": CAMERA_DB[camera_id]["vlan"],
            "stream_link": webrtc_url
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi kết nối media server: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)