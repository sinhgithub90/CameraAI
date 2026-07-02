from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import jwt
import requests
import yaml
import subprocess
import os
import json  # THÊM THƯ VIỆN JSON
import platform
from datetime import datetime, timedelta

app = FastAPI(title="VMS Central API Gateway", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

SECRET_KEY = "CONG_NGHE_AI_QUY_NHON_GIA_LAI"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GO2RTC_YAML_PATH = os.path.join(BASE_DIR, "go2rtc.yaml")
GO2RTC_EXE_PATH = os.path.join(BASE_DIR, "go2rtc.exe")

# File lưu trữ danh sách camera giao diện tập trung
CAMERAS_JSON_PATH = os.path.join(BASE_DIR, "cameras.json")

CAMERA_DB = {
    "cam_huyen_01": {"name": "Camera Ngã tư Huyện 1", "vlan": "VLAN_10", "status": "active"},
    "cam_huyen_02": {"name": "Camera Cổng Ủy Ban Huyện 2", "vlan": "VLAN_10", "status": "active"}
}

DEFAULT_UI_CAMERAS = [
  { "id": "cam_huyen_01", "index": 1, "name": "Hành lang tầng 2", "ip": "10.10.10.12", "model": "Hikvision DS-2CD2143G2", "zone": "Tòa nhà A / Huyện 1", "loc": "Hành lang T2", "type": "video", "src": "http://127.0.0.1:1984/stream.html?src=cam_huyen_01&mode=webrtc", "tag": "Live", "status": "online" },
  { "id": "cam_huyen_02", "index": 2, "name": "Cổng chính cơ quan", "ip": "10.10.10.11", "model": "Hikvision DS-2CD1123G0", "zone": "Khu ngoại vi / Huyện 2", "loc": "Cổng kiểm soát", "type": "video", "src": "http://127.0.0.1:1984/stream.html?src=cam_huyen_02&mode=webrtc", "tag": "Live", "status": "online" },
  { "id": "cam_demo_03", "index": 3, "name": "Sảnh tiếp đón lễ tân", "ip": "192.168.1.53", "model": "Dahua IPC-HFW1230S", "zone": "Tòa nhà B / TP Trung tâm", "loc": "Sảnh chính T1", "type": "image", "src": "https://images.unsplash.com/photo-1497366754035-f200968a6e72?w=500&q=70", "tag": "DEMO", "status": "online" },
  { "id": "cam_demo_04", "index": 4, "name": "Hành lang kỹ thuật T1", "ip": "192.168.1.54", "model": "Dahua IPC-HDBW1230E", "zone": "Tòa nhà B / TP Trung tâm", "loc": "Hành lang cánh tây", "type": "image", "src": "https://images.unsplash.com/photo-1497366811353-6870744d04b2?w=500&q=70", "tag": "DEMO", "status": "online" }
]

class CameraAddInput(BaseModel):
    name: str
    ip: str
    user: str
    password: str
    model: str
    zone: str
    loc: str

def load_ui_cameras():
    if os.path.exists(CAMERAS_JSON_PATH):
        with open(CAMERAS_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_UI_CAMERAS

def save_ui_cameras(cameras):
    with open(CAMERAS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(cameras, f, ensure_ascii=False, indent=2)

# THAY THẾ HOÀN TOÀN HÀM restart_go2rtc_process() TRONG main.py BẰNG ĐOẠN NÀY:
def restart_go2rtc_process():
    try:
        if platform.system() == "Windows":
            # Tắt tiến trình cũ đi
            subprocess.run(["taskkill", "/F", "/IM", "go2rtc.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # ÉP BUỘC: Thêm tham số cwd=BASE_DIR để buộc go2rtc.exe luôn mở đúng thư mục VMS-Center và đọc đúng file cấu hình yaml
            subprocess.Popen([GO2RTC_EXE_PATH], creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=BASE_DIR)
        else:
            subprocess.run(["pkill", "-f", "go2rtc"])
            subprocess.Popen([GO2RTC_EXE_PATH], cwd=BASE_DIR)
        print("-> Đã kích hoạt tái khởi động Go2RTC thành công tại đúng thư mục!")
    except Exception as e:
        print(f"Lỗi khởi động tiến trình Media server: {e}")

@app.post("/api/auth/token")
def login(user: str, text_pass: str):
    if user == "ai_system" and text_pass == "secure_pass_2026":
        payload = {"sub": user, "exp": datetime.utcnow() + timedelta(hours=8), "role": "ai_reader"}
        token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
        return {"access_token": token, "token_type": "bearer"}
    raise HTTPException(status_code=401, detail="Sai tài khoản hoặc mật khẩu")

# API MỚI: Lấy danh sách camera cho Frontend
@app.get("/api/vms/cameras")
def get_all_cameras():
    return load_ui_cameras()

@app.post("/api/vms/camera/add")
def add_camera_and_sync_media(cam: CameraAddInput):
    cameras = load_ui_cameras()
    next_idx = len(cameras) + 1
    cam_id = f"cam_huyen_{next_idx:02d}"
    
    generated_rtsp = f"rtsp://{cam.user}:{cam.password}@{cam.ip}:2004/Streaming/Channels/102"
    CAMERA_DB[cam_id] = {"name": cam.name, "vlan": cam.zone, "status": "active"}
    
    try:
        config_data = {}
        if os.path.exists(GO2RTC_YAML_PATH):
            with open(GO2RTC_YAML_PATH, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f) or {}
                
        if 'streams' not in config_data:
            config_data['streams'] = {}
            
        config_data['streams'][cam_id] = generated_rtsp
        
        with open(GO2RTC_YAML_PATH, 'w', encoding='utf-8') as f:
            yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False)
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Không thể ghi dữ liệu cấu hình YAML: {str(e)}")
        
    # Thêm vào danh sách và lưu lại file JSON
    new_cam_obj = {
        "id": cam_id,
        "index": next_idx,
        "name": cam.name,
        "ip": cam.ip,
        "model": cam.model,
        "zone": cam.zone,
        "loc": cam.loc,
        "type": "video",
        "src": f"http://127.0.0.1:1984/stream.html?src={cam_id}&mode=webrtc",
        "tag": "Live",
        "status": "online"
    }
    cameras.append(new_cam_obj)
    save_ui_cameras(cameras)
    
    restart_go2rtc_process()
    return {"status": "success", "camera": new_cam_obj}

# API MỚI: Xóa camera khỏi hệ thống
@app.delete("/api/vms/camera/{cam_id}")
def delete_camera(cam_id: str):
    cameras = load_ui_cameras()
    cameras = [c for c in cameras if c["id"] != cam_id]
    for idx, c in enumerate(cameras):
        c["index"] = idx + 1
    save_ui_cameras(cameras)
    
    try:
        if os.path.exists(GO2RTC_YAML_PATH):
            with open(GO2RTC_YAML_PATH, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f) or {}
            if 'streams' in config_data and cam_id in config_data['streams']:
                del config_data['streams'][cam_id]
                with open(GO2RTC_YAML_PATH, 'w', encoding='utf-8') as f:
                    yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False)
    except:
        pass
        
    restart_go2rtc_process()
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)