from fastapi import FastAPI, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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
import shutil
from datetime import datetime, timedelta
try:
    import psutil
except ImportError:
    psutil = None

from db import (
    authenticate_user_for_ui,
    create_camera_for_ui,
    create_user_for_ui,
    export_backup_from_db,
    get_user_by_email_for_ui,
    get_recording_file_path,
    get_dashboard_activity_from_db,
    get_dashboard_summary_from_db,
    list_cameras_for_ui,
    list_users_for_ui,
    reset_user_password_for_ui,
    soft_delete_camera_for_ui,
    soft_delete_user_for_ui,
    toggle_user_lock_for_ui,
    update_camera_for_ui,
    update_camera_location_for_ui,
    update_user_for_ui,
    restore_backup_to_db,
    search_recording_segments,
    verify_admin_user_from_db,
)
from recording_manager import recording_manager

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

SETTINGS_JSON_PATH = os.path.join(BASE_DIR, "settings.json")
RECORDINGS_DIR = os.getenv("RECORDINGS_DIR", os.path.join(BASE_DIR, "..", "recordings"))

CAMERA_DB = {
    "cam_huyen_01": {"name": "Camera Ngã tư Huyện 1", "vlan": "VLAN_10", "status": "active"},
    "cam_huyen_02": {"name": "Camera Cổng Ủy Ban Huyện 2", "vlan": "VLAN_10", "status": "active"}
}

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

def remove_go2rtc_stream(stream_key):
    if not os.path.exists(GO2RTC_YAML_PATH):
        return
    with open(GO2RTC_YAML_PATH, "r", encoding="utf-8") as f:
        config_data = yaml.safe_load(f) or {}
    streams = config_data.get("streams")
    if isinstance(streams, dict) and stream_key in streams:
        del streams[stream_key]
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
        username = payload.get("sub")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Phiên đăng nhập hết hạn hoặc Token xác thực không hợp lệ!")

    admin_check = verify_admin_user_from_db(username)
    if admin_check.get("status") == "not_found":
        raise HTTPException(status_code=401, detail="Phiên đăng nhập hết hạn hoặc Token xác thực không hợp lệ!")
    if admin_check.get("status") == "inactive":
        raise HTTPException(status_code=403, detail="Tài khoản hiện đang bị tạm khóa!")
    if admin_check.get("status") != "ok":
        raise HTTPException(status_code=403, detail="TỪ CHỐI TRUY CẬP: Thao tác cấu hình quyền chỉ dành riêng cho Quản trị viên!")
    return admin_check["username"]

@app.post("/api/auth/token")
def login(user: str, text_pass: str):
    if user == "ai_system" and text_pass == "secure_pass_2026":
        payload = {"sub": user, "exp": datetime.utcnow() + timedelta(hours=8), "role": "ai_reader"}
        token = jwt.encode(payload, SECRET_KEY, algorithm="HS256")
        return {"access_token": token, "token_type": "bearer", "user": {"name": "AI Agent Engine", "role": "AI"}}

    current_user = authenticate_user_for_ui(user, text_pass)

    if current_user.get("auth_status") == "inactive":
        raise HTTPException(status_code=403, detail="Tài khoản hiện đang bị tạm khóa!")

    if current_user.get("auth_status") == "ok":
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
    current_user = get_user_by_email_for_ui(email)
    if current_user.get("auth_status") == "inactive":
        raise HTTPException(status_code=403, detail="Tài khoản liên kết với Email này hiện đang bị tạm khóa!")
    if current_user.get("auth_status") == "ok":
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

class CameraUpdateInput(BaseModel):
    name: Optional[str] = None
    ip: Optional[str] = None
    user: Optional[str] = None
    password: Optional[str] = None
    model: Optional[str] = None
    zone: Optional[str] = None
    loc: Optional[str] = None
    resolution: Optional[str] = None
    fps: Optional[int] = None
    bitrate: Optional[int] = None
    codec: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None

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

@app.put("/api/vms/camera/{cam_id}")
def update_camera(cam_id: str, data: CameraUpdateInput):
    updates = data.model_dump(exclude_unset=True) if hasattr(data, "model_dump") else data.dict(exclude_unset=True)
    try:
        updated_camera = update_camera_for_ui(cam_id, updates, sync_go2rtc_stream)
    except Exception as e:
        print(f"PostgreSQL camera update error: {e}")
        raise HTTPException(status_code=500, detail=f"Khong the cap nhat camera trong PostgreSQL: {str(e)}")
    if not updated_camera:
        raise HTTPException(status_code=404, detail="Không tìm thấy camera")
    if any(key in updates for key in ("ip", "user", "password")):
        restart_go2rtc_process()
    return {"status": "success", "camera": updated_camera}

@app.delete("/api/vms/camera/{cam_id}")
def delete_camera(cam_id: str):
    try:
        stream_key = soft_delete_camera_for_ui(cam_id, remove_go2rtc_stream)
    except Exception as e:
        print(f"PostgreSQL camera delete error: {e}")
        raise HTTPException(status_code=500, detail=f"Khong the xoa camera trong PostgreSQL: {str(e)}")
    if not stream_key:
        raise HTTPException(status_code=404, detail="Không tìm thấy camera")
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
    try:
        return list_users_for_ui()
    except Exception as exc:
        print(f"PostgreSQL user query error: {exc}")
        raise HTTPException(
            status_code=503,
            detail=(
                "Khong the doc danh sach nguoi dung tu PostgreSQL. "
                "Kiem tra DATABASE_URL, PostgreSQL server va schema multicamai."
            ),
        )

class UserAddInput(BaseModel):
    username: str; name: str; role: str; unit: str; email: str; phone: str; password: str

@app.post("/api/vms/user/add")
def add_new_user(user: UserAddInput, admin_user: str = Depends(verify_admin_role)):
    result = create_user_for_ui(user)
    if result.get("status") == "duplicate_username":
        raise HTTPException(status_code=400, detail="Tên đăng nhập đã tồn tại!")
    if result.get("status") == "duplicate_email":
        raise HTTPException(status_code=400, detail="Email đã tồn tại!")
    if result.get("status") == "role_not_found":
        raise HTTPException(status_code=400, detail="Vai trò người dùng không hợp lệ!")
    if result.get("status") == "invalid_username":
        raise HTTPException(status_code=400, detail="Tên đăng nhập không hợp lệ!")
    if result.get("status") == "invalid_name":
        raise HTTPException(status_code=400, detail="Họ tên không hợp lệ!")
    return {"status": "success"}

class UserUpdateInput(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    unit: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    permissions: Optional[list] = None

@app.put("/api/vms/user/{username}")
def update_user_profile(username: str, data: UserUpdateInput, admin_user: str = Depends(verify_admin_role)):
    result = update_user_for_ui(username, data)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
    if result.get("status") == "duplicate_email":
        raise HTTPException(status_code=400, detail="Email đã tồn tại!")
    if result.get("status") == "role_not_found":
        raise HTTPException(status_code=400, detail="Vai trò người dùng không hợp lệ!")
    if result.get("status") == "invalid_name":
        raise HTTPException(status_code=400, detail="Họ tên không hợp lệ!")
    return {"status": "success"}

@app.post("/api/vms/user/{username}/reset-password")
def reset_password(username: str, admin_user: str = Depends(verify_admin_role)):
    new_password = "123456abc"
    result = reset_user_password_for_ui(username, new_password, admin_user)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
    return {"status": "success", "new_password": new_password}

@app.post("/api/vms/user/{username}/toggle-lock")
def toggle_lock_user(username: str, admin_user: str = Depends(verify_admin_role)):
    result = toggle_user_lock_for_ui(username, admin_user)
    if result.get("status") == "last_admin":
        raise HTTPException(status_code=400, detail="Không được phép khóa tài khoản Admin tối cao!")
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
    return {"status": "success", "new_status": result["new_status"]}

@app.delete("/api/vms/user/{username}")
def delete_user(username: str, admin_user: str = Depends(verify_admin_role)):
    result = soft_delete_user_for_ui(username, admin_user)
    if result.get("status") == "last_admin":
        raise HTTPException(status_code=400, detail="Không được phép xóa tài khoản Admin tối cao!")
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng")
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


@app.on_event("startup")
def startup_recording_manager():
    try:
        recording_manager.start()
    except Exception as exc:
        print(f"Recording manager startup error: {exc}")


@app.on_event("shutdown")
def shutdown_recording_manager():
    recording_manager.shutdown()


def _parse_range_header(range_header, file_size):
    if not range_header:
        return None
    unit, _, range_value = range_header.partition("=")
    if unit.strip().lower() != "bytes" or "-" not in range_value:
        return None

    start_text, end_text = range_value.split("-", 1)
    try:
        if start_text == "":
            suffix_length = int(end_text)
            if suffix_length <= 0:
                return None
            start = max(file_size - suffix_length, 0)
            end = file_size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else file_size - 1
    except ValueError:
        return None

    if start < 0 or start >= file_size or end < start:
        return None
    return start, min(end, file_size - 1)


def _iter_file_range(file_path, start, end, chunk_size=1024 * 1024):
    with open(file_path, "rb") as video_file:
        video_file.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = video_file.read(min(chunk_size, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@app.get("/api/playback/search")
def search_playback(
    camera_id: Optional[str] = None,
    from_time: Optional[str] = None,
    to_time: Optional[str] = None,
    zone: Optional[str] = None,
    loc: Optional[str] = None,
):
    try:
        segments = search_recording_segments(
            camera_id=camera_id,
            zone=zone,
            loc=loc,
            from_time=from_time,
            to_time=to_time,
        )
    except Exception as exc:
        print(f"PostgreSQL playback search error: {exc}")
        raise HTTPException(status_code=500, detail=f"Khong the truy van video phat lai: {str(exc)}")

    gaps = []
    previous_by_camera = {}
    for segment in segments:
        previous = previous_by_camera.get(segment["camera_id"])
        if previous:
            previous_end = datetime.fromisoformat(previous["end_time"])
            current_start = datetime.fromisoformat(segment["start_time"])
            gap_seconds = int((current_start - previous_end).total_seconds())
            if gap_seconds > 10:
                gaps.append(
                    {
                        "camera_id": segment["camera_id"],
                        "from_time": previous["end_time"],
                        "to_time": segment["start_time"],
                        "gap_seconds": gap_seconds,
                    }
                )
        previous_by_camera[segment["camera_id"]] = segment

    return {"segments": segments, "gaps": gaps, "count": len(segments)}


@app.get("/api/playback/file/{segment_id}")
def stream_playback_file(segment_id: int, request: Request):
    file_path = get_recording_file_path(segment_id)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Không tìm thấy file video phát lại")

    file_size = os.path.getsize(file_path)
    if file_size <= 0:
        raise HTTPException(status_code=404, detail="File video phát lại không hợp lệ")

    range_value = _parse_range_header(request.headers.get("range"), file_size)
    if range_value:
        start, end = range_value
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Content-Length": str(end - start + 1),
            "Content-Disposition": f'inline; filename="{os.path.basename(file_path)}"',
        }
        return StreamingResponse(
            _iter_file_range(file_path, start, end),
            status_code=206,
            media_type="video/mp4",
            headers=headers,
        )

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(file_size),
        "Content-Disposition": f'inline; filename="{os.path.basename(file_path)}"',
    }
    return StreamingResponse(
        _iter_file_range(file_path, 0, file_size - 1),
        media_type="video/mp4",
        headers=headers,
    )


@app.get("/api/recording/status")
def get_recording_status():
    return recording_manager.status()


@app.post("/api/recording/{camera_id}/start")
def start_recording_camera(camera_id: str):
    result = recording_manager.start_camera(camera_id)
    if result.get("status") == "not_found":
        raise HTTPException(status_code=404, detail="Khong tim thay camera bat ghi hinh")
    return result


@app.post("/api/recording/{camera_id}/stop")
def stop_recording_camera(camera_id: str):
    return recording_manager.stop_camera(camera_id)


@app.post("/api/recording/reload")
def reload_recording_manager():
    return recording_manager.reload()


def _disk_usage(path):
    target = path if os.path.exists(path) else os.path.dirname(path) or "."
    usage = shutil.disk_usage(target)
    return {
        "path": path,
        "total": usage.total,
        "used": usage.used,
        "free": usage.free,
        "percent": round((usage.used / usage.total) * 100, 2) if usage.total else 0,
        "exists": os.path.exists(path),
    }


@app.get("/api/dashboard/summary")
def get_dashboard_summary():
    try:
        summary = get_dashboard_summary_from_db()
    except Exception as exc:
        print(f"PostgreSQL dashboard summary error: {exc}")
        raise HTTPException(status_code=500, detail=f"Khong the doc du lieu dashboard: {str(exc)}")
    summary["disk"] = _disk_usage(RECORDINGS_DIR)
    return summary


@app.get("/api/dashboard/activity")
def get_dashboard_activity(limit: int = 20):
    try:
        return {"items": get_dashboard_activity_from_db(limit)}
    except Exception as exc:
        print(f"PostgreSQL dashboard activity error: {exc}")
        raise HTTPException(status_code=500, detail=f"Khong the doc nhat ky dashboard: {str(exc)}")


@app.get("/api/dashboard/system")
def get_dashboard_system():
    disk = _disk_usage(RECORDINGS_DIR)
    recording_status = recording_manager.status()
    if psutil is None:
        return {
            "psutil_available": False,
            "cpu": None,
            "ram": None,
            "disk": disk,
            "recording_manager": recording_status,
        }
    memory = psutil.virtual_memory()
    return {
        "psutil_available": True,
        "cpu": {"percent": psutil.cpu_percent(interval=0.1)},
        "ram": {
            "total": memory.total,
            "used": memory.used,
            "free": memory.available,
            "percent": memory.percent,
        },
        "disk": disk,
        "recording_manager": recording_status,
    }


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
    db_backup = export_backup_from_db()
    return {
        "exported_at": datetime.utcnow().isoformat(),
        "cameras": db_backup["cameras"],
        "users": db_backup["users"],
        "settings": load_settings()
    }

class BackupRestoreInput(BaseModel):
    cameras: Optional[list] = None
    users: Optional[list] = None
    settings: Optional[dict] = None

@app.post("/api/vms/backup/restore", dependencies=[Depends(verify_admin_role)])
def restore_backup(data: BackupRestoreInput):
    """Phục hồi cấu hình từ gói backup đã xuất trước đó"""
    try:
        restore_result = restore_backup_to_db(data.cameras, data.users)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        print(f"PostgreSQL backup restore error: {exc}")
        raise HTTPException(status_code=500, detail=f"Khong the phuc hoi backup vao PostgreSQL: {str(exc)}")

    for stream_key, rtsp_url in restore_result.get("streams_to_sync", []):
        sync_go2rtc_stream(stream_key, rtsp_url)
    if restore_result.get("streams_to_sync"):
        restart_go2rtc_process()

    if data.settings is not None:
        save_settings(data.settings)
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
