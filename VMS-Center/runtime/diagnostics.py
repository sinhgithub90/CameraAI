import os
import subprocess
import time

import requests

from db import db_cursor


REQUIRED_TABLES = {
    "camera",
    "ket_noi_camera",
    "tinh_trang_camera",
    "lich_su_trang_thai_camera",
    "nhat_ky_he_thong",
    "nhat_ky_camera",
    "doan_video",
    "nguoi_dung",
    "xac_thuc_nguoi_dung",
}


class Diagnostics:
    def __init__(self, config):
        self.config = config

    def run(self):
        checks = [
            self._check_postgresql(),
            self._check_schema(),
            self._check_recordings_dir(),
            self._check_executable("ffmpeg", self.config.ffmpeg_path, ["-version"]),
            self._check_executable("ffprobe", self.config.ffprobe_path, ["-version"]),
            self._check_go2rtc_executable(),
            self._check_go2rtc_api(),
        ]
        status = self._overall_status(checks)
        return {
            "status": status,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "checks": checks,
        }

    def _overall_status(self, checks):
        if any(item["status"] == "error" for item in checks):
            return "error"
        if any(item["status"] == "warning" for item in checks):
            return "warning"
        return "ok"

    def _result(self, name, status, message, suggestion=None, details=None):
        return {
            "name": name,
            "status": status,
            "message": message,
            "suggestion": suggestion,
            "details": details or {},
        }

    def _check_postgresql(self):
        try:
            with db_cursor() as cur:
                cur.execute("select current_database() as database, current_schema() as schema")
                row = dict(cur.fetchone())
            return self._result("postgresql", "ok", "PostgreSQL ket noi duoc", details=row)
        except Exception as exc:
            return self._result(
                "postgresql",
                "error",
                str(exc),
                "Kiem tra DATABASE_URL va PostgreSQL service.",
            )

    def _check_schema(self):
        try:
            with db_cursor() as cur:
                cur.execute(
                    """
                    select table_name
                    from information_schema.tables
                    where table_schema = 'multicamai'
                    """
                )
                tables = {row["table_name"] for row in cur.fetchall()}
            missing = sorted(REQUIRED_TABLES - tables)
            if missing:
                return self._result(
                    "schema",
                    "error",
                    f"Thieu bang bat buoc: {', '.join(missing)}",
                    "Chay migration/schema PostgreSQL truoc khi start backend.",
                    {"missing": missing},
                )
            return self._result("schema", "ok", "Schema multicamai co du bang bat buoc")
        except Exception as exc:
            return self._result("schema", "error", str(exc), "Kiem tra PostgreSQL/schema multicamai.")

    def _check_recordings_dir(self):
        path = self.config.recordings_dir
        if not os.path.exists(path):
            return self._result(
                "recordings_dir",
                "warning",
                f"Thu muc recording chua ton tai: {path}",
                "StartupManager se tao thu muc neu co quyen ghi.",
                {"path": path},
            )
        if not os.path.isdir(path):
            return self._result("recordings_dir", "error", f"Khong phai thu muc: {path}", details={"path": path})
        if not os.access(path, os.W_OK):
            return self._result(
                "recordings_dir",
                "error",
                f"Khong co quyen ghi: {path}",
                "Cap quyen ghi hoac doi RECORDINGS_DIR.",
                {"path": path},
            )
        return self._result("recordings_dir", "ok", "RECORDINGS_DIR san sang", details={"path": path})

    def _check_executable(self, name, path, args):
        command = [path] + args
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=8,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode == 0:
                first_line = (result.stdout or result.stderr).splitlines()[0] if (result.stdout or result.stderr) else ""
                return self._result(name, "ok", f"{name} chay duoc", details={"path": path, "version": first_line})
            return self._result(
                name,
                "error",
                f"{name} tra ve ma loi {result.returncode}: {result.stderr.strip()}",
                f"Kiem tra {name.upper()}_PATH.",
                {"path": path, "command": command},
            )
        except Exception as exc:
            return self._result(
                name,
                "error",
                str(exc),
                f"Khai bao {name.upper()}_PATH den file exe co quyen chay.",
                {"path": path, "command": command, "exception_type": type(exc).__name__},
            )

    def _check_go2rtc_executable(self):
        if not self.config.go2rtc_enabled:
            return self._result("go2rtc_executable", "warning", "GO2RTC_ENABLED=false")
        path = self.config.go2rtc_exe_path
        if not os.path.exists(path):
            return self._result(
                "go2rtc_executable",
                "error",
                f"Khong tim thay go2rtc.exe: {path}",
                "Cau hinh GO2RTC_EXE_PATH dung file go2rtc.exe.",
                {"path": path},
            )
        return self._result("go2rtc_executable", "ok", "go2rtc.exe ton tai", details={"path": path})

    def _check_go2rtc_api(self):
        url = f"{self.config.go2rtc_api_url}/api/streams"
        try:
            response = requests.get(url, timeout=2)
            if response.ok:
                return self._result(
                    "go2rtc_api",
                    "ok",
                    "go2rtc API phan hoi",
                    details={"url": url, "status_code": response.status_code},
                )
            return self._result(
                "go2rtc_api",
                "warning",
                f"go2rtc API tra status {response.status_code}",
                "Kiem tra go2rtc config/API.",
                {"url": url, "status_code": response.status_code},
            )
        except Exception as exc:
            return self._result(
                "go2rtc_api",
                "warning",
                str(exc),
                "Neu GO2RTC_ENABLED=true, StartupManager se thu start go2rtc.",
                {"url": url, "exception_type": type(exc).__name__},
            )
