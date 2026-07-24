import os

from .config import load_runtime_config
from .diagnostics import Diagnostics
from .process_manager import ProcessManager
from .runtime_state import RuntimeState


class StartupManager:
    def __init__(self, health_manager, recording_manager):
        self.config = load_runtime_config()
        self.health_manager = health_manager
        self.recording_manager = recording_manager
        self.process_manager = ProcessManager(self.config)
        self.diagnostics = Diagnostics(self.config)
        self.state = RuntimeState()

    def start(self):
        self.state.set_status("starting")
        diagnostics = self.run_diagnostics()
        if diagnostics["status"] == "error" and self.config.startup_strict_mode:
            self.state.set_status("error")
            raise RuntimeError("Runtime diagnostics failed in strict mode")

        go2rtc_result = self.start_go2rtc()
        self.state.set_go2rtc(
            go2rtc_result.get("status"),
            owned=go2rtc_result.get("owned"),
            pid=go2rtc_result.get("pid"),
        )

        if self.config.health_auto_start:
            self.health_manager.start()
            self.state.set_health(self.health_manager.status().get("status"))
        else:
            self.state.set_health("disabled")

        if self.config.recording_auto_start and self._can_start_recording(diagnostics):
            os.makedirs(self.config.recordings_dir, exist_ok=True)
            self.recording_manager.start()
            self.state.set_recording(self.recording_manager.status().get("status"))
        elif self.config.recording_auto_start:
            self.state.set_recording("skipped_diagnostics")
        else:
            self.state.set_recording("disabled")

        self._recompute_status()
        return self.status()

    def _can_start_recording(self, diagnostics):
        blocking = {"ffmpeg", "recordings_dir"}
        for check in diagnostics.get("checks", []):
            if check.get("name") in blocking and check.get("status") == "error":
                return False
        return True

    def shutdown(self):
        self.state.set_status("stopping")
        self.health_manager.shutdown()
        self.state.set_health("stopped")
        self.recording_manager.shutdown()
        self.state.set_recording("stopped")
        stop_result = self.process_manager.stop_go2rtc()
        self.state.set_go2rtc(stop_result.get("status"), owned=False)
        self.state.set_status("stopped")
        return self.status()

    def reload(self):
        diagnostics = self.run_diagnostics()
        if self.config.health_auto_start:
            self.health_manager.start()
            self.state.set_health(self.health_manager.status().get("status"))
        if self.config.recording_auto_start and self._can_start_recording(diagnostics):
            self.recording_manager.reload()
            self.state.set_recording(self.recording_manager.status().get("status"))
        elif self.config.recording_auto_start:
            self.state.set_recording("skipped_diagnostics")
        self._recompute_status()
        return self.status()

    def run_diagnostics(self):
        result = self.diagnostics.run()
        self.state.set_diagnostics(result)
        self._recompute_status()
        return result

    def start_go2rtc(self):
        result = self.process_manager.start_go2rtc()
        self.state.set_go2rtc(result.get("status"), owned=result.get("owned"), pid=result.get("pid"))
        self._recompute_status()
        return result

    def stop_go2rtc(self):
        result = self.process_manager.stop_go2rtc()
        self.state.set_go2rtc(result.get("status"), owned=False)
        self._recompute_status()
        return result

    def restart_go2rtc(self):
        result = self.process_manager.restart_go2rtc()
        start = result.get("start") or {}
        self.state.set_go2rtc(start.get("status"), owned=start.get("owned"), pid=start.get("pid"))
        self._recompute_status()
        return result

    def _recompute_status(self):
        current = self.state.snapshot().get("status")
        if current in {"stopped", "stopping"}:
            return current

        diagnostics = self.state.snapshot().get("diagnostics") or {}
        checks = diagnostics.get("checks") or []
        errors = [item for item in checks if item.get("status") == "error"]
        warnings = [item for item in checks if item.get("status") == "warning"]

        go2rtc_status = self.process_manager.status()
        health_status = self.health_manager.status()
        recording_status = self.recording_manager.status()

        self.state.set_health(health_status.get("status"))
        self.state.set_recording(recording_status.get("status"))
        self.state.set_go2rtc(
            "ready" if go2rtc_status.get("api_ready") else self.state.snapshot().get("go2rtc", {}).get("status"),
            owned=go2rtc_status.get("owned"),
            pid=go2rtc_status.get("pid"),
        )

        if errors:
            self.state.set_status("error")
        elif warnings:
            self.state.set_status("degraded")
        elif self.config.go2rtc_enabled and not go2rtc_status.get("api_ready"):
            self.state.set_status("degraded")
        elif self.config.health_auto_start and health_status.get("status") != "running":
            self.state.set_status("degraded")
        elif self.config.recording_auto_start and recording_status.get("status") != "running":
            self.state.set_status("degraded")
        else:
            self.state.set_status("running")
        return self.state.snapshot().get("status")

    def status(self):
        snapshot = self.state.snapshot()
        snapshot["config"] = {
            "go2rtc_enabled": self.config.go2rtc_enabled,
            "go2rtc_exe_path": self.config.go2rtc_exe_path,
            "go2rtc_config_path": self.config.go2rtc_config_path,
            "go2rtc_api_url": self.config.go2rtc_api_url,
            "ffmpeg_path": self.config.ffmpeg_path,
            "ffprobe_path": self.config.ffprobe_path,
            "recordings_dir": self.config.recordings_dir,
            "recording_auto_start": self.config.recording_auto_start,
            "health_auto_start": self.config.health_auto_start,
            "startup_strict_mode": self.config.startup_strict_mode,
        }
        snapshot["processes"] = {"go2rtc": self.process_manager.status()}
        return snapshot
