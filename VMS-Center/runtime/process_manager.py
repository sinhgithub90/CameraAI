import os
import subprocess
import time

import requests

try:
    import psutil
except ImportError:
    psutil = None


class ProcessManager:
    def __init__(self, config):
        self.config = config
        self.go2rtc_process = None
        self.go2rtc_owned = False

    def go2rtc_api_ready(self):
        url = f"{self.config.go2rtc_api_url}/api/streams"
        try:
            response = requests.get(url, timeout=2)
            return response.ok
        except Exception:
            return False

    def find_go2rtc_processes(self):
        if psutil is None:
            return []
        matches = []
        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                name = (proc.info.get("name") or "").lower()
                exe = (proc.info.get("exe") or "").lower()
                if "go2rtc" in name or "go2rtc" in exe:
                    matches.append({"pid": proc.info.get("pid"), "name": proc.info.get("name"), "exe": proc.info.get("exe")})
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return matches

    def start_go2rtc(self):
        if not self.config.go2rtc_enabled:
            return {"status": "disabled", "owned": False, "pid": None}
        if self.go2rtc_api_ready():
            return {"status": "already_running", "owned": False, "pid": None}
        external = self.find_go2rtc_processes()
        if external:
            return {"status": "external_running", "owned": False, "pid": external[0].get("pid"), "processes": external}
        if not os.path.exists(self.config.go2rtc_exe_path):
            return {"status": "error", "error": f"go2rtc.exe not found: {self.config.go2rtc_exe_path}", "owned": False}

        command = [self.config.go2rtc_exe_path]
        cwd = os.path.dirname(self.config.go2rtc_config_path) or os.path.dirname(self.config.go2rtc_exe_path)
        try:
            self.go2rtc_process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self.go2rtc_owned = True
        except Exception as exc:
            return {"status": "error", "error": str(exc), "owned": False}

        ready = self.wait_go2rtc_ready(timeout_seconds=10)
        return {
            "status": "started" if ready else "started_not_ready",
            "owned": True,
            "pid": self.go2rtc_process.pid,
            "api_ready": ready,
        }

    def wait_go2rtc_ready(self, timeout_seconds=10):
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self.go2rtc_api_ready():
                return True
            time.sleep(0.5)
        return False

    def stop_go2rtc(self):
        if not self.go2rtc_owned or not self.go2rtc_process:
            return {"status": "not_owned", "message": "Runtime khong dung process go2rtc ngoai quyen so huu"}
        if self.go2rtc_process.poll() is not None:
            self.go2rtc_owned = False
            return {"status": "already_stopped", "pid": self.go2rtc_process.pid}
        pid = self.go2rtc_process.pid
        try:
            self.go2rtc_process.terminate()
            self.go2rtc_process.wait(timeout=8)
        except Exception:
            self.go2rtc_process.kill()
        finally:
            self.go2rtc_owned = False
        return {"status": "stopped", "pid": pid}

    def restart_go2rtc(self):
        stop_result = self.stop_go2rtc()
        start_result = self.start_go2rtc()
        return {"stop": stop_result, "start": start_result}

    def status(self):
        api_ready = self.go2rtc_api_ready()
        owned_running = bool(
            self.go2rtc_process
            and self.go2rtc_owned
            and self.go2rtc_process.poll() is None
        )
        return {
            "api_ready": api_ready,
            "owned": self.go2rtc_owned,
            "owned_running": owned_running,
            "pid": self.go2rtc_process.pid if self.go2rtc_process else None,
            "external_processes": self.find_go2rtc_processes(),
        }
