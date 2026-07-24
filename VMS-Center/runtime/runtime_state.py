from datetime import datetime
from threading import RLock


class RuntimeState:
    def __init__(self):
        self.lock = RLock()
        self.status = "stopped"
        self.started_at = None
        self.updated_at = None
        self.diagnostics = None
        self.go2rtc = {"owned": False, "pid": None, "status": "unknown"}
        self.health = {"status": "stopped"}
        self.recording = {"status": "stopped"}
        self.errors = []
        self.warnings = []

    def set_status(self, status):
        with self.lock:
            self.status = status
            self.updated_at = datetime.now().astimezone()
            if status in {"running", "degraded"} and self.started_at is None:
                self.started_at = self.updated_at

    def set_diagnostics(self, diagnostics):
        with self.lock:
            self.diagnostics = diagnostics
            self.updated_at = datetime.now().astimezone()
            self.errors = [item for item in diagnostics.get("checks", []) if item.get("status") == "error"]
            self.warnings = [item for item in diagnostics.get("checks", []) if item.get("status") == "warning"]

    def set_go2rtc(self, status, owned=None, pid=None):
        with self.lock:
            self.go2rtc["status"] = status
            if owned is not None:
                self.go2rtc["owned"] = owned
            if pid is not None:
                self.go2rtc["pid"] = pid
            self.updated_at = datetime.now().astimezone()

    def set_health(self, status):
        with self.lock:
            self.health["status"] = status
            self.updated_at = datetime.now().astimezone()

    def set_recording(self, status):
        with self.lock:
            self.recording["status"] = status
            self.updated_at = datetime.now().astimezone()

    def snapshot(self):
        with self.lock:
            return {
                "status": self.status,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "updated_at": self.updated_at.isoformat() if self.updated_at else None,
                "go2rtc": dict(self.go2rtc),
                "health": dict(self.health),
                "recording": dict(self.recording),
                "diagnostics": self.diagnostics,
                "error_count": len(self.errors),
                "warning_count": len(self.warnings),
            }
