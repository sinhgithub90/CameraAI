import os
import glob
import json
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

from alert_service import alert_service

try:
    import psutil
except ImportError:
    psutil = None

from db import (
    get_camera_health_for_ui,
    get_health_summary_from_db,
    list_camera_health_targets,
    record_camera_health_result,
)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, "..", ".env"))


class HealthManager:
    def __init__(self):
        self.probe_timeout = float(os.getenv("HEALTH_PROBE_TIMEOUT", "1.0"))
        self.poll_interval = float(os.getenv("HEALTH_POLL_INTERVAL", "10"))
        self.fail_threshold = int(os.getenv("HEALTH_FAIL_THRESHOLD", "2"))
        self.ffprobe_timeout = float(os.getenv("HEALTH_FFPROBE_TIMEOUT", "5"))
        self.recording_segment_max_age = int(os.getenv("HEALTH_RECORDING_SEGMENT_MAX_AGE_SECONDS", "150"))
        self.go2rtc_base_url = os.getenv("GO2RTC_BASE_URL", "http://127.0.0.1:1984").rstrip("/")
        self.default_rtsp_port = int(os.getenv("DEFAULT_RTSP_PORT", "554"))
        self.ffprobe_path = self._resolve_executable("FFPROBE_PATH", "ffprobe")
        self.fail_counter = {}
        self.last_camera_results = {}
        self.recording_status_provider = None
        self.last_run_at = None
        self.last_error = None
        self.thread = None
        self.stop_event = threading.Event()
        self.lock = threading.RLock()

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()

    def set_recording_status_provider(self, provider):
        self.recording_status_provider = provider

    def shutdown(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)

    def status(self):
        with self.lock:
            return {
                "status": "running" if self.thread and self.thread.is_alive() else "stopped",
                "poll_interval": self.poll_interval,
                "probe_timeout": self.probe_timeout,
                "ffprobe_timeout": self.ffprobe_timeout,
                "fail_threshold": self.fail_threshold,
                "recording_segment_max_age": self.recording_segment_max_age,
                "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
                "last_error": self.last_error,
            }

    def check_once(self):
        results = []
        go2rtc_status = self.go2rtc()
        recording_status = self._recording_status()
        for camera in list_camera_health_targets():
            result = self._check_camera(camera, go2rtc_status, recording_status)
            results.append(result)
        with self.lock:
            self.last_camera_results = {item["camera_id"]: item for item in results}
            self.last_run_at = datetime.now().astimezone()
            self.last_error = None
        return results

    def summary(self):
        summary = get_health_summary_from_db()
        summary["manager"] = self.status()
        return summary

    def cameras(self):
        items = get_camera_health_for_ui()
        with self.lock:
            latest = dict(self.last_camera_results)
        for item in items:
            signals = latest.get(item.get("camera_id"))
            if signals:
                item["signals"] = signals.get("signals")
                item["classification_reason"] = signals.get("reason")
                item["fail_count"] = signals.get("fail_count")
        return {"items": items, "manager": self.status()}

    def go2rtc(self):
        api = self._check_go2rtc_api()
        process = self._check_process("go2rtc")
        return {
            "base_url": self.go2rtc_base_url,
            "api": api,
            "process": process,
            "healthy": bool(api.get("ok") or process.get("running")),
        }

    def system(self, disk_path):
        disk = self._disk_usage(disk_path)
        if psutil is None:
            return {
                "psutil_available": False,
                "cpu": None,
                "ram": None,
                "disk": disk,
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
        }

    def _run_loop(self):
        while not self.stop_event.is_set():
            try:
                self.check_once()
            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc)
                print(f"Health manager error: {exc}")
            self.stop_event.wait(self.poll_interval)

    def _resolve_executable(self, env_name, executable_name):
        configured = os.getenv(env_name)
        if configured:
            return configured
        found = shutil.which(executable_name)
        if found:
            return found
        if os.name == "nt":
            local_app_data = os.getenv("LOCALAPPDATA")
            if local_app_data:
                pattern = os.path.join(
                    local_app_data,
                    "Microsoft",
                    "WinGet",
                    "Packages",
                    "Gyan.FFmpeg*",
                    "**",
                    "bin",
                    f"{executable_name}.exe",
                )
                matches = sorted(glob.glob(pattern, recursive=True))
                if matches:
                    return matches[-1]
                package_dirs = sorted(
                    glob.glob(
                        os.path.join(
                            local_app_data,
                            "Microsoft",
                            "WinGet",
                            "Packages",
                            "Gyan.FFmpeg*",
                        )
                    )
                )
                for package_dir in reversed(package_dirs):
                    build_dirs = sorted(glob.glob(os.path.join(package_dir, "ffmpeg-*full_build")))
                    if build_dirs:
                        return os.path.join(build_dirs[-1], "bin", f"{executable_name}.exe")
        return executable_name

    def _check_camera(self, camera, go2rtc_status, recording_status):
        started = time.perf_counter()
        host, port = self._rtsp_target(camera)
        stream_key = camera.get("stream_key")
        rtsp_signal = self._check_rtsp(camera, host, port)
        live_signal = self._live_signal(stream_key, go2rtc_status)
        recording_signal = self._recording_signal(stream_key, camera.get("bat_ghi_hinh"), recording_status)

        camera_reachable = bool(rtsp_signal.get("ok"))
        live_available = bool(live_signal.get("ok"))
        recording_available = recording_signal.get("ok")
        source_available = camera_reachable or live_available

        if source_available:
            self.fail_counter[stream_key] = 0
            if live_available and recording_signal.get("expected") and not recording_available:
                status_value = "RECORDING_ERROR"
                reason = "Live available but recording is not healthy"
            elif (camera_reachable and not live_available) or (
                recording_signal.get("expected") and recording_available is False
            ):
                status_value = "DEGRADED"
                reason = "Camera source reachable but one dependent service is degraded"
            else:
                status_value = "ONLINE"
                reason = "Live or RTSP source is healthy"
        else:
            self.fail_counter[stream_key] = self.fail_counter.get(stream_key, 0) + 1
            if self.fail_counter[stream_key] < self.fail_threshold:
                status_value = camera.get("trang_thai_hien_tai") or "UNKNOWN"
                reason = "RTSP and live failed, waiting for threshold"
            else:
                status_value = "OFFLINE"
                reason = "RTSP and live failed past threshold"
        if status_value not in {"ONLINE", "OFFLINE"}:
            if status_value not in {"DEGRADED", "RECORDING_ERROR"}:
                status_value = "OFFLINE"

        write_result = record_camera_health_result(
            camera["db_id"],
            status_value,
            source_available,
            rtsp_signal.get("latency_ms") or live_signal.get("latency_ms"),
            reason=reason,
        )
        alert_result = None
        if status_value == "OFFLINE" and self.fail_counter.get(stream_key, 0) >= self.fail_threshold:
            alert_result = alert_service.create_alert(
                source="HEALTH",
                severity="HIGH",
                camera_id=camera["db_id"],
                khu_vuc_id=camera.get("khu_vuc_id"),
                title=f"Camera offline: {camera.get('name') or stream_key}",
                description=reason,
                duplicate_key=f"HEALTH:CAMERA_OFFLINE:{stream_key}",
            )
        return {
            "camera_id": stream_key,
            "host": host,
            "port": port,
            "camera_reachable": camera_reachable,
            "live_available": live_available,
            "recording_available": recording_available,
            "status": status_value,
            "latency_ms": int(round((time.perf_counter() - started) * 1000)),
            "signals": {
                "camera_reachable": rtsp_signal,
                "live_available": live_signal,
                "recording_available": recording_signal,
            },
            "reason": reason,
            "alert": alert_result,
            "fail_count": self.fail_counter.get(stream_key, 0),
            "changed": write_result.get("changed", False),
        }

    def _rtsp_target(self, camera):
        rtsp_url = camera.get("rtsp_url") or ""
        parsed = urlparse(rtsp_url)
        if parsed.hostname:
            return parsed.hostname, parsed.port or self.default_rtsp_port
        return camera.get("ip") or "127.0.0.1", camera.get("cong_rtsp") or self.default_rtsp_port

    def _check_rtsp(self, camera, host, port):
        rtsp_url = camera.get("rtsp_url")
        if rtsp_url:
            result = self._ffprobe_rtsp(rtsp_url)
            if result.get("ok"):
                return result
            fallback = self._tcp_reachable(host, port)
            fallback["method"] = "tcp_fallback"
            fallback["ffprobe"] = result
            return fallback
        result = self._tcp_reachable(host, port)
        result["method"] = "tcp_fallback_no_rtsp_url"
        return result

    def _ffprobe_rtsp(self, rtsp_url):
        started = time.perf_counter()
        command = [
            self.ffprobe_path,
            "-v",
            "error",
            "-rtsp_transport",
            "tcp",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            rtsp_url,
        ]
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.ffprobe_timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            latency_ms = int(round((time.perf_counter() - started) * 1000))
            ok = False
            streams = []
            if result.returncode == 0 and result.stdout.strip():
                payload = json.loads(result.stdout)
                streams = payload.get("streams") or []
                ok = bool(streams)
            return {
                "ok": ok,
                "method": "ffprobe",
                "latency_ms": latency_ms if ok else None,
                "stream_count": len(streams),
                "error": None if ok else (result.stderr.strip() or f"ffprobe exited {result.returncode}"),
            }
        except Exception as exc:
            return {"ok": False, "method": "ffprobe", "latency_ms": None, "stream_count": 0, "error": str(exc)}

    def _tcp_reachable(self, host, port):
        started = time.perf_counter()
        try:
            with socket.create_connection((host, int(port)), timeout=self.probe_timeout):
                return {
                    "ok": True,
                    "method": "tcp",
                    "latency_ms": int(round((time.perf_counter() - started) * 1000)),
                    "error": None,
                }
        except (socket.timeout, ConnectionRefusedError, OSError, ValueError) as exc:
            return {"ok": False, "method": "tcp", "latency_ms": None, "error": str(exc)}

    def _check_go2rtc_api(self):
        url = f"{self.go2rtc_base_url}/api/streams"
        started = time.perf_counter()
        try:
            response = requests.get(url, timeout=2)
            latency_ms = int(round((time.perf_counter() - started) * 1000))
            return {
                "ok": response.ok,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
                "url": url,
                "streams": response.json() if response.ok else None,
            }
        except Exception as exc:
            return {"ok": False, "status_code": None, "latency_ms": None, "url": url, "error": str(exc)}

    def _live_signal(self, stream_key, go2rtc_status):
        api = go2rtc_status.get("api") or {}
        streams = api.get("streams")
        if not api.get("ok") or not isinstance(streams, dict):
            return {
                "ok": False,
                "method": "go2rtc_api",
                "checked": bool(api),
                "latency_ms": api.get("latency_ms"),
                "error": api.get("error") or "go2rtc API unavailable",
            }
        stream_data = streams.get(stream_key)
        if stream_data is None:
            return {
                "ok": False,
                "method": "go2rtc_api",
                "checked": True,
                "latency_ms": api.get("latency_ms"),
                "error": "stream key not found",
            }
        producers = stream_data.get("producers") if isinstance(stream_data, dict) else None
        consumers = stream_data.get("consumers") if isinstance(stream_data, dict) else None
        active = bool(producers or consumers or stream_data)
        return {
            "ok": active,
            "method": "go2rtc_api",
            "checked": True,
            "latency_ms": api.get("latency_ms"),
            "producer_count": len(producers or []),
            "consumer_count": len(consumers or []),
            "error": None if active else "stream exists but inactive",
        }

    def _recording_status(self):
        if not self.recording_status_provider:
            return {"status": "unknown", "cameras": []}
        try:
            return self.recording_status_provider() or {"status": "unknown", "cameras": []}
        except Exception as exc:
            return {"status": "error", "cameras": [], "error": str(exc)}

    def _recording_signal(self, stream_key, recording_enabled, recording_status):
        if not recording_enabled:
            return {"ok": None, "expected": False, "method": "recording_manager", "error": None}

        cameras = recording_status.get("cameras") or []
        state = next((item for item in cameras if item.get("camera_id") == stream_key), None)
        if not state:
            return {
                "ok": False,
                "expected": True,
                "method": "recording_manager",
                "running": False,
                "fresh_segment": False,
                "error": "recording process not found",
            }

        running = bool(state.get("running"))
        last_segment_at = state.get("last_segment_at")
        fresh_segment = False
        if last_segment_at:
            try:
                parsed = datetime.fromisoformat(last_segment_at)
                fresh_segment = datetime.now(parsed.tzinfo) - parsed <= timedelta(seconds=self.recording_segment_max_age)
            except ValueError:
                fresh_segment = False
        ok = running and fresh_segment
        return {
            "ok": ok,
            "expected": True,
            "method": "recording_manager",
            "running": running,
            "fresh_segment": fresh_segment,
            "pid": state.get("pid"),
            "last_segment_at": last_segment_at,
            "last_segment_id": state.get("last_segment_id"),
            "error": None if ok else state.get("last_error") or "recording process has no fresh segment",
        }

    def _check_process(self, process_name):
        if psutil is not None:
            matches = []
            for proc in psutil.process_iter(["pid", "name", "exe"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    exe = (proc.info.get("exe") or "").lower()
                    if process_name.lower() in name or process_name.lower() in exe:
                        matches.append({"pid": proc.info.get("pid"), "name": proc.info.get("name")})
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return {"checked_by": "psutil", "running": bool(matches), "items": matches}

        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {process_name}.exe"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
            running = f"{process_name}.exe" in result.stdout.lower()
            return {"checked_by": "tasklist", "running": running, "items": []}
        return {"checked_by": "none", "running": None, "items": []}

    def _disk_usage(self, path):
        target = path if os.path.exists(path) else os.path.dirname(path) or "."
        usage = os.statvfs(target) if hasattr(os, "statvfs") else None
        if usage:
            total = usage.f_frsize * usage.f_blocks
            free = usage.f_frsize * usage.f_bavail
            used = total - free
        else:
            import shutil

            disk = shutil.disk_usage(target)
            total, used, free = disk.total, disk.used, disk.free
        return {
            "path": path,
            "total": total,
            "used": used,
            "free": free,
            "percent": round((used / total) * 100, 2) if total else 0,
            "exists": os.path.exists(path),
        }


health_manager = HealthManager()
