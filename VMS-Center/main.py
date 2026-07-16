from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
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

from db import create_camera_for_ui, list_cameras_for_ui, update_camera_location_for_ui

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
SETTINGS_JSON_PATH = os.path.join(BASE_DIR, "settings.json")

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

DEFAULT_SETTINGS = {
    "notifications": {
        "email_enabled": True,
        "email_address": "admin@multicamai.local",
        "sms_enabled": False,
        "sms_phone": "",
        "min_alert_level": "trung_binh"   # thap | trung_binh | cao
    },
    "integration": {
        "go2rtc_api_url": "http://127.0.0.1:1984",
        "webhook_url": "",
        "webhook_enabled": False
    },
    "security": {
        "session_timeout_hours": 8,
        "min_password_length": 6,
        "force_password_change_days": 0   # 0 = tắt
    },
    "backup": {
        "auto_backup_enabled": False,
        "auto_backup_interval_hours": 24
    }
}

def load_settings():
    if os.path.exists(SETTINGS_JSON_PATH):
        with open(SETTINGS_JSON_PATH, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                # Đảm bảo đủ field mặc định nếu file cũ thiếu key mới thêm sau này
                merged = json.loads(json.dumps(DEFAULT_SETTINGS))
                for section, values in data.items():
                    if section in merged and isinstance(values, dict):
                        merged[section].update(values)
                    else:
                        merged[section] = values
                return merged
            except Exception:
                pass
    save_settings(DEFAULT_SETTINGS)
    return DEFAULT_SETTINGS

def save_settings(settings):
    with open(SETTINGS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)

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

def sync_go2rtc_stream(stream_key, rtsp_url):
    config_data = {}
    if os.path.exists(GO2RTC_YAML_PATH):
        with open(GO2RTC_YAML_PATH, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f) or {}
    if "streams" not in config_data or not isinstance(config_data["streams"], dict):
        config_data["streams"] = {}
    config_data["streams"][stream_key] = rtsp_url
    with open(GO2RTC_YAML_PATH, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False)

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
    try:
        return list_cameras_for_ui()
    except Exception as exc:
        print(f"PostgreSQL camera query error: {exc}")
        raise HTTPException(
            status_code=503,
            detail=(
                "Khong the doc danh sach camera tu PostgreSQL. "
                "Kiem tra DATABASE_URL, PostgreSQL server va schema multicamai."
            ),
        )

class CameraAddInput(BaseModel):
    name: str; ip: str; user: str; password: str; model: str; zone: str; loc: str

@app.post("/api/vms/camera/add")
def add_camera_and_sync_media(cam: CameraAddInput):
    try:
        new_cam_obj = create_camera_for_ui(cam, sync_go2rtc_stream)
    except Exception as e:
        print(f"PostgreSQL camera create error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Khong the tao camera trong PostgreSQL hoac dong bo go2rtc: {str(e)}",
        )
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
    if update_camera_location_for_ui(cam_id, data.lat, data.lng):
        print(f"GIS: Da gan toa do ({data.lat}, {data.lng}) cho camera {cam_id}")
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
        from db import db_cursor

        time.sleep(5)
        fail_counter = {}

        while True:
            try:
                cameras = list_cameras_for_ui()
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

                status_updates = []
                for cam in video_cams:
                    cam_id = cam["id"]
                    is_online = online_map.get(cam_id, False)

                    if is_online:
                        fail_counter[cam_id] = 0
                        status_updates.append(("ONLINE", cam_id))
                    else:
                        fail_counter[cam_id] = fail_counter.get(cam_id, 0) + 1
                        if fail_counter[cam_id] >= FAIL_THRESHOLD:
                            status_updates.append(("OFFLINE", cam_id))

                if status_updates:
                    with db_cursor(commit=True) as cur:
                        for status_value, stream_key in status_updates:
                            cur.execute(
                                """
                                update camera
                                set trang_thai_hien_tai = %s
                                where stream_key = %s
                                  and deleted_at is null
                                  and trang_thai_hien_tai is distinct from %s
                                """,
                                (status_value, stream_key, status_value),
                            )
            except Exception as e:
                print(f"Lỗi health check: {e}")
            time.sleep(POLL_INTERVAL)

    t = threading.Thread(target=background_sync, daemon=True)
    t.start()

start_go2rtc_proxy_sync()
load_users()


@app.get("/api/vms/settings")
def get_settings():
    return load_settings()

class NotificationSettingsInput(BaseModel):
    email_enabled: bool
    email_address: str
    sms_enabled: bool
    sms_phone: str
    min_alert_level: str

@app.put("/api/vms/settings/notifications", dependencies=[Depends(verify_admin_role)])
def update_notification_settings(data: NotificationSettingsInput):
    settings = load_settings()
    settings["notifications"] = data.dict()
    save_settings(settings)
    return {"status": "success"}

class IntegrationSettingsInput(BaseModel):
    go2rtc_api_url: str
    webhook_url: str
    webhook_enabled: bool

@app.put("/api/vms/settings/integration", dependencies=[Depends(verify_admin_role)])
def update_integration_settings(data: IntegrationSettingsInput):
    settings = load_settings()
    settings["integration"] = data.dict()
    save_settings(settings)
    return {"status": "success"}

class SecuritySettingsInput(BaseModel):
    session_timeout_hours: int
    min_password_length: int
    force_password_change_days: int

@app.put("/api/vms/settings/security", dependencies=[Depends(verify_admin_role)])
def update_security_settings(data: SecuritySettingsInput):
    settings = load_settings()
    settings["security"] = data.dict()
    save_settings(settings)
    return {"status": "success"}

class BackupSettingsInput(BaseModel):
    auto_backup_enabled: bool
    auto_backup_interval_hours: int

@app.put("/api/vms/settings/backup", dependencies=[Depends(verify_admin_role)])
def update_backup_settings(data: BackupSettingsInput):
    settings = load_settings()
    settings["backup"] = data.dict()
    save_settings(settings)
    return {"status": "success"}

@app.post("/api/vms/system/restart-media", dependencies=[Depends(verify_admin_role)])
def restart_media_server():
    restart_go2rtc_process()
    return {"status": "success", "message": "Đã gửi lệnh khởi động lại Media Server (go2rtc)"}

@app.get("/api/vms/backup/export", dependencies=[Depends(verify_admin_role)])
def export_backup():
    """Xuất toàn bộ cấu hình hệ thống (camera, người dùng, cài đặt) thành 1 gói JSON duy nhất"""
    return {
        "exported_at": datetime.utcnow().isoformat(),
        "cameras": load_ui_cameras(),
        "users": load_users(),
        "settings": load_settings()
    }

class BackupRestoreInput(BaseModel):
    cameras: Optional[list] = None
    users: Optional[list] = None
    settings: Optional[dict] = None

@app.post("/api/vms/backup/restore", dependencies=[Depends(verify_admin_role)])
def restore_backup(data: BackupRestoreInput):
    """Phục hồi cấu hình từ gói backup đã xuất trước đó"""
    if data.cameras is not None:
        save_ui_cameras(data.cameras)
    if data.users is not None:
        save_users(data.users)
    if data.settings is not None:
        save_settings(data.settings)
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
