from fastapi import FastAPI, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import jwt
import requests
import yaml
import subprocess
import os
import json  
import shutil
import platform
import threading
import time
from datetime import datetime, timedelta

from db import (
    get_recording_file_path,
    list_cameras_for_ui,
    search_recording_segments,
    upsert_recording_segment,
)

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
RECORDINGS_DIR = os.path.abspath(
    os.getenv("RECORDINGS_DIR") or os.path.join(BASE_DIR, "..", "recordings")
)
RECORDING_SEGMENT_SECONDS = int(os.getenv("RECORDING_SEGMENT_SECONDS", "60"))
FFMPEG_EXE = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg")
if not FFMPEG_EXE:
    winget_packages_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    if os.path.exists(winget_packages_dir):
        for root, _dirs, files in os.walk(winget_packages_dir):
            if "ffmpeg.exe" in files:
                FFMPEG_EXE = os.path.join(root, "ffmpeg.exe")
                break
recording_processes = {}
recording_lock = threading.Lock()

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

def load_go2rtc_streams():
    if not os.path.exists(GO2RTC_YAML_PATH):
        return {}
    with open(GO2RTC_YAML_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("streams") or {}

def get_camera_lookup():
    try:
        return {cam["id"]: cam for cam in list_cameras_for_ui()}
    except Exception as exc:
        print(f"Database camera lookup fallback: {exc}")
        return {cam["id"]: cam for cam in load_ui_cameras()}

def parse_segment_start(file_path):
    stem = os.path.splitext(os.path.basename(file_path))[0]
    try:
        return datetime.strptime(stem, "%Y%m%d_%H%M%S")
    except ValueError:
        return None

def index_recording_file(file_path, camera):
    start_dt = parse_segment_start(file_path)
    if not start_dt:
        return
    try:
        size = os.path.getsize(file_path)
        modified_age = time.time() - os.path.getmtime(file_path)
    except OSError:
        return
    if size < 1024 * 1024 or modified_age < 10:
        return
    end_dt = start_dt + timedelta(seconds=RECORDING_SEGMENT_SECONDS)
    if camera.get("db_id"):
        upsert_recording_segment(
            camera,
            file_path,
            start_dt,
            end_dt,
            RECORDING_SEGMENT_SECONDS,
            size,
        )

def scan_recording_index():
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    cameras = get_camera_lookup()
    for camera_id, camera in cameras.items():
        camera_dir = os.path.join(RECORDINGS_DIR, camera_id)
        if not os.path.exists(camera_dir):
            continue
        for root, _dirs, files in os.walk(camera_dir):
            for name in files:
                if name.lower().endswith((".mp4", ".mkv")):
                    index_recording_file(os.path.join(root, name), camera)

def build_recording_output_pattern(camera_id):
    return os.path.join(RECORDINGS_DIR, camera_id, "%Y%m%d_%H%M%S.mp4")

def start_camera_recorder(camera_id, rtsp_url):
    if not FFMPEG_EXE:
        return {"ok": False, "error": "ffmpeg_not_found"}
    os.makedirs(os.path.join(RECORDINGS_DIR, camera_id), exist_ok=True)
    log_path = os.path.join(RECORDINGS_DIR, camera_id, "ffmpeg.log")
    output_pattern = build_recording_output_pattern(camera_id)
    cmd = [
        FFMPEG_EXE,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-rtsp_transport",
        "tcp",
        "-i",
        rtsp_url,
        "-c",
        "copy",
        "-f",
        "segment",
        "-segment_time",
        str(RECORDING_SEGMENT_SECONDS),
        "-reset_timestamps",
        "1",
        "-strftime",
        "1",
        "-strftime_mkdir",
        "1",
        output_pattern,
    ]
    try:
        log_file = open(log_path, "ab")
        process = subprocess.Popen(cmd, stdout=log_file, stderr=log_file, cwd=BASE_DIR)
        with recording_lock:
            recording_processes[camera_id] = {
                "process": process,
                "started_at": datetime.now().isoformat(),
                "rtsp_url": rtsp_url,
                "log_path": log_path,
            }
        return {"ok": True, "pid": process.pid}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

def stop_camera_recorder(camera_id):
    with recording_lock:
        info = recording_processes.pop(camera_id, None)
    if not info:
        return
    process = info["process"]
    if process.poll() is None:
        process.terminate()

def recording_supervisor_loop():
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    while True:
        try:
            streams = load_go2rtc_streams()
            cameras = get_camera_lookup()
            for camera_id, rtsp_url in streams.items():
                if camera_id not in cameras:
                    continue
                with recording_lock:
                    info = recording_processes.get(camera_id)
                    running = bool(info and info["process"].poll() is None)
                if not running:
                    start_camera_recorder(camera_id, rtsp_url)
            scan_recording_index()
        except Exception as exc:
            print(f"Recording supervisor error: {exc}")
        time.sleep(10)

def start_recording_supervisor():
    thread = threading.Thread(target=recording_supervisor_loop, daemon=True)
    thread.start()

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
        print(f"Database camera fallback: {exc}")
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
@app.get("/api/recording/status")
def get_recording_status():
    cameras = get_camera_lookup()
    with recording_lock:
        process_snapshot = dict(recording_processes)
    camera_status = []
    for camera in cameras.values():
        info = process_snapshot.get(camera["id"])
        running = bool(info and info["process"].poll() is None)
        camera_status.append(
            {
                "camera_id": camera["id"],
                "camera_name": camera.get("name"),
                "running": running,
                "pid": info["process"].pid if info else None,
                "started_at": info.get("started_at") if info else None,
                "log_path": info.get("log_path") if info else None,
            }
        )
    return {
        "ffmpeg_available": bool(FFMPEG_EXE),
        "ffmpeg_path": FFMPEG_EXE,
        "recordings_dir": RECORDINGS_DIR,
        "segment_seconds": RECORDING_SEGMENT_SECONDS,
        "cameras": camera_status,
    }

@app.post("/api/recording/restart")
def restart_recording():
    with recording_lock:
        camera_ids = list(recording_processes)
    for camera_id in camera_ids:
        stop_camera_recorder(camera_id)
    return {"status": "restarting"}

@app.get("/api/playback/search")
def search_playback(
    camera_id: str | None = None,
    zone: str | None = None,
    loc: str | None = None,
    from_time: str | None = Query(default=None),
    to_time: str | None = Query(default=None),
):
    return search_recording_segments(camera_id, zone, loc, from_time, to_time)

def iter_file_bytes(file_path, start=0, end=None, chunk_size=1024 * 1024):
    with open(file_path, "rb") as f:
        f.seek(start)
        remaining = None if end is None else end - start + 1
        while True:
            read_size = chunk_size if remaining is None else min(chunk_size, remaining)
            if read_size <= 0:
                break
            data = f.read(read_size)
            if not data:
                break
            yield data
            if remaining is not None:
                remaining -= len(data)

@app.get("/api/playback/file/{segment_id}")
def get_playback_file(segment_id: int, request: Request):
    file_path = get_recording_file_path(segment_id)
    if not file_path:
        raise HTTPException(status_code=404, detail="Khong tim thay doan video")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File video khong con ton tai tren may")
    file_size = os.path.getsize(file_path)
    range_header = request.headers.get("range")
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Disposition": f'inline; filename="{os.path.basename(file_path)}"',
    }
    if range_header and range_header.startswith("bytes="):
        start_text, _, end_text = range_header.replace("bytes=", "", 1).partition("-")
        start = int(start_text) if start_text else 0
        end = int(end_text) if end_text else file_size - 1
        end = min(end, file_size - 1)
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"
        headers["Content-Length"] = str(end - start + 1)
        return StreamingResponse(
            iter_file_bytes(file_path, start, end),
            status_code=206,
            media_type="video/mp4",
            headers=headers,
        )
    headers["Content-Length"] = str(file_size)
    return StreamingResponse(iter_file_bytes(file_path), media_type="video/mp4", headers=headers)

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
start_recording_supervisor()
load_users()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
