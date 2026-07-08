from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import jwt
import requests
import yaml
import subprocess
import os
import json  
import platform
import threading
import time
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

CAMERAS_JSON_PATH = os.path.join(BASE_DIR, "cameras.json")
USERS_JSON_PATH = os.path.join(BASE_DIR, "users.json")

CAMERA_DB = {
    "cam_huyen_01": {"name": "Camera Ngã tư Huyện 1", "vlan": "VLAN_10", "status": "active"},
    "cam_huyen_02": {"name": "Camera Cổng Ủy Ban Huyện 2", "vlan": "VLAN_10", "status": "active"}
}

DEFAULT_UI_CAMERAS = [
  { "id": "cam_huyen_01", "index": 1, "name": "Hành lang tầng 2", "ip": "10.10.10.12", "model": "Hikvision DS-2CD2143G2", "zone": "Tòa nhà A / Huyện 1", "loc": "Hành lang T2", "type": "video", "src": "http://127.0.0.1:1984/stream.html?src=cam_huyen_01&mode=webrtc", "tag": "Live", "status": "online" },
  { "id": "cam_huyen_02", "index": 2, "name": "Cổng chính cơ quan", "ip": "10.10.10.11", "model": "Hikvision DS-2CD1123G0", "zone": "Khu ngoại vi / Huyện 2", "loc": "Cổng kiểm soát", "type": "video", "src": "http://127.0.0.1:1984/stream.html?src=cam_huyen_02&mode=webrtc", "tag": "Live", "status": "online" }
]

DEFAULT_USERS = [
    {
        "username": "admin",
        "password": "admin1",
        "name": "Trần Văn Minh",
        "role": "Quản trị viên",
        "unit": "Phòng CNTT",
        "email": "minh.tran@demo.com",
        "status": "Hoạt động",
        "phone": "0909 123 456",
        "permissions": ["live", "playback", "cammgmt", "usermgmt", "alertmgmt", "reports", "sysconfig", "export"]
    }
]

def load_ui_cameras():
    if os.path.exists(CAMERAS_JSON_PATH):
        with open(CAMERAS_JSON_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_UI_CAMERAS

def save_ui_cameras(cameras):
    with open(CAMERAS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(cameras, f, ensure_ascii=False, indent=2)

def load_users():
    if os.path.exists(USERS_JSON_PATH):
        with open(USERS_JSON_PATH, "r", encoding="utf-8") as f:
            try: return json.load(f)
            except: pass
    with open(USERS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_USERS, f, ensure_ascii=False, indent=2)
    return DEFAULT_USERS

def save_users(users):
    with open(USERS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

def restart_go2rtc_process():
    try:
        if platform.system() == "Windows":
            subprocess.run(["taskkill", "/F", "/IM", "go2rtc.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.Popen([GO2RTC_EXE_PATH], creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=BASE_DIR)
        else:
            subprocess.run(["pkill", "-f", "go2rtc"])
            subprocess.Popen([GO2RTC_EXE_PATH], cwd=BASE_DIR)
    except Exception as e:
        print(f"Lỗi khởi động tiến trình Media server: {e}")

def verify_admin_role(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        if payload.get("role") != "Quản trị viên":
            raise HTTPException(status_code=403, detail="TỪ CHỐI TRUY CẬP: Thao tác cấu hình quyền chỉ dành riêng cho Quản trị viên!")
        return payload.get("sub")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Phiên đăng nhập hết hạn hoặc Token xác thực không hợp lệ!")

@app.post("/api/auth/token")
def login(user: str, text_pass: str):
    if user == "ai_system" and text_pass == "secure_pass_2026":
        payload = {"sub": user, "exp": datetime.utcnow() + timedelta(hours=8), "role": "ai_reader"}
        token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
        return {"access_token": token, "token_type": "bearer", "user": {"name": "AI Agent Engine", "role": "AI"}}

    users = load_users()
    current_user = next((u for u in users if u["username"] == user and u["password"] == text_pass), None)
    
    if current_user:
        if current_user["status"] != "Hoạt động":
            raise HTTPException(status_code=403, detail="Tài khoản hiện đang bị tạm khóa!")
            
        payload = {"sub": user, "exp": datetime.utcnow() + timedelta(hours=8), "role": current_user["role"]}
        token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "username": current_user["username"], "name": current_user["name"],
                "role": current_user["role"], "unit": current_user["unit"], "permissions": current_user["permissions"]
            }
        }
    raise HTTPException(status_code=401, detail="Sai tài khoản hoặc mật khẩu đăng nhập!")

@app.post("/api/auth/google-mock")
def google_mock_login(email: str):
    users = load_users()
    current_user = next((u for u in users if u["email"].lower() == email.strip().lower()), None)
    if current_user:
        if current_user["status"] != "Hoạt động":
            raise HTTPException(status_code=403, detail="Tài khoản liên kết với Email này hiện đang bị tạm khóa!")
        payload = {"sub": current_user["username"], "exp": datetime.utcnow() + timedelta(hours=8), "role": current_user["role"]}
        token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "username": current_user["username"], "name": current_user["name"],
                "role": current_user["role"], "unit": current_user["unit"], "permissions": current_user["permissions"]
            }
        }
    raise HTTPException(status_code=404, detail="Email Google này chưa được quy hoạch đăng ký trong hệ thống!")

@app.get("/api/vms/cameras")
def get_all_cameras():
    return load_ui_cameras()

class CameraAddInput(BaseModel):
    name: str; ip: str; user: str; password: str; model: str; zone: str; loc: str

@app.post("/api/vms/camera/add")
def add_camera_and_sync_media(cam: CameraAddInput):
    cameras = load_ui_cameras()
    next_idx = len(cameras) + 1
    cam_id = f"cam_huyen_{next_idx:02d}"
    generated_rtsp = f"rtsp://{cam.user}:{cam.password}@{cam.ip}:2004/Streaming/Channels/102"
    
    try:
        config_data = {}
        if os.path.exists(GO2RTC_YAML_PATH):
            with open(GO2RTC_YAML_PATH, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f) or {}
        if 'streams' not in config_data: config_data['streams'] = {}
        config_data['streams'][cam_id] = generated_rtsp
        with open(GO2RTC_YAML_PATH, 'w', encoding='utf-8') as f:
            yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Lỗi ghi cấu hình: {str(e)}")
        
    new_cam_obj = {
        "id": cam_id, "index": next_idx, "name": cam.name, "ip": cam.ip, "model": cam.model,
        "zone": cam.zone, "loc": cam.loc, "type": "video",
        "src": f"http://127.0.0.1:1984/stream.html?src={cam_id}&mode=webrtc", "tag": "Live", "status": "online"
    }
    cameras.append(new_cam_obj)
    save_ui_cameras(cameras)
    restart_go2rtc_process()
    return {"status": "success", "camera": new_cam_obj}

@app.delete("/api/vms/camera/{cam_id}")
def delete_camera(cam_id: str):
    cameras = load_ui_cameras()
    cameras = [c for c in cameras if c["id"] != cam_id]
    for idx, c in enumerate(cameras): c["index"] = idx + 1
    save_ui_cameras(cameras)
    try:
        if os.path.exists(GO2RTC_YAML_PATH):
            with open(GO2RTC_YAML_PATH, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f) or {}
            if 'streams' in config_data and cam_id in config_data['streams']:
                del config_data['streams'][cam_id]
                with open(GO2RTC_YAML_PATH, 'w', encoding='utf-8') as f:
                    yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False)
    except: pass
    restart_go2rtc_process()
    return {"status": "success"}

# THÊM ĐOẠN NÀY VÀO TRONG FILE main.py
class CameraLocationInput(BaseModel):
    lat: float
    lng: float


@app.put("/api/vms/camera/{cam_id}/location")
def update_camera_location(cam_id: str, data: CameraLocationInput, admin_user: str = Depends(verify_admin_role)):
    cameras = load_ui_cameras()
    is_changed = False
    for c in cameras:
        if c["id"] == cam_id:
            c["lat"] = data.lat
            c["lng"] = data.lng
            is_changed = True
            break
            
    if is_changed:
        save_ui_cameras(cameras)
        print(f"🗺️ GIS: Đã gán tọa độ ({data.lat}, {data.lng}) cho camera {cam_id}")
        return {"status": "success"}
        
    raise HTTPException(status_code=404, detail="Không tìm thấy camera")

@app.get("/api/vms/users")
def get_all_users():
    return load_users()

class UserAddInput(BaseModel):
    username: str; name: str; role: str; unit: str; email: str; phone: str; password: str

@app.post("/api/vms/user/add")
def add_new_user(user: UserAddInput, admin_user: str = Depends(verify_admin_role)):
    users = load_users()
    if any(u["username"] == user.username for u in users):
        raise HTTPException(status_code=400, detail="Tên đăng nhập đã tồn tại!")
    new_user_obj = {
        "username": user.username, "password": user.password, "name": user.name, "role": user.role,
        "unit": user.unit, "email": user.email, "status": "Hoạt động", "phone": user.phone, "permissions": ["live"]
    }
    users.append(new_user_obj)
    save_users(users)
    return {"status": "success"}

class UserUpdateInput(BaseModel):
    name: str; role: str; unit: str; email: str; phone: str; permissions: list

@app.put("/api/vms/user/{username}")
def update_user_profile(username: str, data: UserUpdateInput, admin_user: str = Depends(verify_admin_role)):
    users = load_users()
    for u in users:
        if u["username"] == username:
            u["name"] = data.name; u["role"] = data.role; u["unit"] = data.unit
            u["email"] = data.email; u["phone"] = data.phone; u["permissions"] = data.permissions
            save_users(users)
            return {"status": "success"}
    raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")

@app.post("/api/vms/user/{username}/reset-password")
def reset_password(username: str, admin_user: str = Depends(verify_admin_role)):
    users = load_users()
    for u in users:
        if u["username"] == username:
            u["password"] = "123456abc"
            save_users(users)
            return {"status": "success", "new_password": u["password"]}
    raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")

@app.post("/api/vms/user/{username}/toggle-lock")
def toggle_lock_user(username: str, admin_user: str = Depends(verify_admin_role)):
    if username == "admin":
        raise HTTPException(status_code=400, detail="Không được phép khóa tài khoản Admin tối cao!")
    users = load_users()
    for u in users:
        if u["username"] == username:
            u["status"] = "Tạm khóa" if u["status"] == "Hoạt động" else "Hoạt động"
            save_users(users)
            return {"status": "success", "new_status": u["status"]}
    raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")

@app.delete("/api/vms/user/{username}", dependencies=[Depends(verify_admin_role)])
def delete_user(username: str):
    if username == "admin":
        raise HTTPException(status_code=400, detail="Không được phép xóa tài khoản Admin tối cao!")
    users = load_users()
    filtered_users = [u for u in users if u["username"] != username]
    save_users(filtered_users)
    return {"status": "success"}

# 🌟 LUỒNG QUYÉT NGẦM TRUNG GIAN (PROXY HEALTH CHECK)
# THAY THẾ HÀM start_go2rtc_proxy_sync TRONG main.py
def start_go2rtc_proxy_sync():
    import socket
    from urllib.parse import urlparse
    from concurrent.futures import ThreadPoolExecutor

    # Có thể chỉnh các tham số này để cân bằng giữa "phát hiện nhanh"
    # và "chống chớp tắt do rớt gói mạng tạm thời"
    PROBE_TIMEOUT = 1.0      # giây - thời gian chờ tối đa mỗi lần dò cổng RTSP
    POLL_INTERVAL = 1.0      # giây - khoảng cách giữa các vòng quét
    FAIL_THRESHOLD = 2       # số lần thất bại liên tiếp mới xác nhận offline
    DEFAULT_RTSP_PORT = 554  # fallback nếu không tìm thấy trong go2rtc.yaml

    def get_rtsp_targets():
        """Đọc go2rtc.yaml để lấy đúng host:port RTSP thật của từng camera.
        Không dùng port cố định vì mỗi camera có thể khai báo port RTSP khác nhau
        (VD: cam_huyen_01 dùng 2004, cam_huyen_02 dùng 2005)."""
        targets = {}
        try:
            if os.path.exists(GO2RTC_YAML_PATH):
                with open(GO2RTC_YAML_PATH, 'r', encoding='utf-8') as f:
                    config_data = yaml.safe_load(f) or {}
                for cam_id, url in (config_data.get('streams') or {}).items():
                    parsed = urlparse(url)
                    if parsed.hostname:
                        targets[cam_id] = (parsed.hostname, parsed.port or DEFAULT_RTSP_PORT)
        except Exception as e:
            print(f"Lỗi đọc go2rtc.yaml: {e}")
        return targets

    def check_camera_reachable(host, port, timeout=PROBE_TIMEOUT):
        """Kiểm tra camera có mở cổng RTSP không - độc lập với go2rtc"""
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

    def background_sync():
        time.sleep(5)
        fail_counter = {}

        while True:
            try:
                cameras = load_ui_cameras()
                video_cams = [c for c in cameras if c.get("type") == "video"]
                rtsp_targets = get_rtsp_targets()  # đọc lại mỗi vòng để nhận camera mới thêm

                def probe(cam):
                    cam_id = cam["id"]
                    host, port = rtsp_targets.get(cam_id, (cam["ip"], DEFAULT_RTSP_PORT))
                    return cam_id, check_camera_reachable(host, port)

                # Quét song song tất cả camera trong cùng 1 vòng, thay vì tuần tự,
                # để tổng thời gian 1 vòng quét không phụ thuộc số lượng camera
                with ThreadPoolExecutor(max_workers=max(len(video_cams), 1)) as pool:
                    results = list(pool.map(probe, video_cams))
                online_map = dict(results)

                is_changed = False
                for cam in video_cams:
                    cam_id = cam["id"]
                    is_online = online_map.get(cam_id, False)

                    if is_online:
                        fail_counter[cam_id] = 0
                        if cam.get("status") != "online":
                            cam["status"] = "online"
                            is_changed = True
                    else:
                        fail_counter[cam_id] = fail_counter.get(cam_id, 0) + 1
                        if fail_counter[cam_id] >= FAIL_THRESHOLD and cam.get("status") != "offline":
                            cam["status"] = "offline"
                            is_changed = True

                if is_changed:
                    save_ui_cameras(cameras)
            except Exception as e:
                print(f"Lỗi health check: {e}")
            time.sleep(POLL_INTERVAL)

    t = threading.Thread(target=background_sync, daemon=True)
    t.start()

start_go2rtc_proxy_sync()
load_users()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)