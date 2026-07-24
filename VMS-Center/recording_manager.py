import os
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv

from db import list_recording_enabled_cameras, upsert_recording_segment


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, "..", ".env"))


class RecordingManager:
    def __init__(self):
        self.recordings_dir = os.getenv("RECORDINGS_DIR")
        self.segment_seconds = int(os.getenv("RECORDING_SEGMENT_SECONDS", "60"))
        self.ffmpeg_path = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"
        self.ffprobe_path = os.getenv("FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe"
        self.processes = {}
        self.cameras = {}
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.thread = None

    def start(self):
        if not self.recordings_dir:
            raise RuntimeError("RECORDINGS_DIR chua duoc cau hinh trong .env")
        os.makedirs(self.recordings_dir, exist_ok=True)
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()

    def shutdown(self):
        self.stop_event.set()
        with self.lock:
            stream_keys = list(self.processes.keys())
        for stream_key in stream_keys:
            self.stop_camera(stream_key)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)

    def reload(self):
        desired = self._load_desired_cameras()
        with self.lock:
            current = set(self.processes.keys())
        desired_keys = set(desired.keys())
        for stream_key in current - desired_keys:
            self.stop_camera(stream_key)
        for stream_key in desired_keys:
            self.start_camera(stream_key)
        return self.status()

    def start_camera(self, camera_id):
        desired = self._load_desired_cameras()
        camera = desired.get(camera_id)
        if not camera:
            return {"status": "not_found"}

        with self.lock:
            state = self.processes.get(camera_id)
            if state and self._is_process_alive(state.get("process")):
                return {"status": "already_running", "pid": state["process"].pid}
            self.cameras[camera_id] = camera
            state = self._spawn_process(camera)
            self.processes[camera_id] = state
            return {"status": "started", "pid": state["process"].pid}

    def stop_camera(self, camera_id):
        with self.lock:
            state = self.processes.pop(camera_id, None)
        if not state:
            return {"status": "not_running"}
        self._terminate_process(state.get("process"))
        self._close_log_file(state)
        return {"status": "stopped"}

    def status(self):
        with self.lock:
            items = []
            for stream_key, state in sorted(self.processes.items()):
                process = state.get("process")
                camera = state.get("camera") or {}
                items.append(
                    {
                        "camera_id": stream_key,
                        "camera_name": camera.get("name"),
                        "pid": process.pid if process else None,
                        "running": self._is_process_alive(process),
                        "state": state.get("state"),
                        "last_error": state.get("last_error"),
                        "retry_count": state.get("retry_count", 0),
                        "next_retry_at": state.get("next_retry_at").isoformat() if state.get("next_retry_at") else None,
                        "last_segment_path": state.get("last_segment_path"),
                        "last_segment_id": state.get("last_segment_id"),
                        "last_segment_at": state.get("last_segment_at").isoformat() if state.get("last_segment_at") else None,
                    }
                )
            return {
                "status": "running" if self.thread and self.thread.is_alive() else "stopped",
                "recordings_dir": self.recordings_dir,
                "segment_seconds": self.segment_seconds,
                "ffmpeg_path": self.ffmpeg_path,
                "cameras": items,
            }

    def _run_loop(self):
        last_reload = 0
        while not self.stop_event.is_set():
            now = time.time()
            if now - last_reload >= 10:
                self._reconcile()
                last_reload = now
            self._scan_completed_segments()
            self._restart_failed_processes()
            self.stop_event.wait(2)

    def _load_desired_cameras(self):
        cameras = {}
        for camera in list_recording_enabled_cameras():
            stream_key = camera.get("stream_key")
            if stream_key:
                cameras[stream_key] = camera
        return cameras

    def _reconcile(self):
        desired = self._load_desired_cameras()
        with self.lock:
            current = set(self.processes.keys())
            for stream_key, camera in desired.items():
                self.cameras[stream_key] = camera
                if stream_key not in self.processes:
                    self.processes[stream_key] = self._spawn_process(camera)
            for stream_key in current - set(desired.keys()):
                state = self.processes.pop(stream_key, None)
                if state:
                    self._terminate_process(state.get("process"))

    def _spawn_process(self, camera):
        stream_key = camera["stream_key"]
        output_dir = os.path.join(self.recordings_dir, stream_key)
        os.makedirs(output_dir, exist_ok=True)
        log_path = os.path.join(output_dir, "ffmpeg.log")
        output_pattern = os.path.join(output_dir, "%Y%m%d_%H%M%S.mp4")
        command = [
            self.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-rtsp_transport",
            "tcp",
            "-i",
            camera["rtsp_url"],
            "-map",
            "0",
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(self.segment_seconds),
            "-reset_timestamps",
            "1",
            "-strftime",
            "1",
            "-segment_format",
            "mp4",
            output_pattern,
        ]
        log_file = open(log_path, "ab")
        try:
            process = subprocess.Popen(
                command,
                stdout=log_file,
                stderr=log_file,
                cwd=BASE_DIR,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            last_error = None
            state = "recording"
        except Exception as exc:
            process = None
            last_error = str(exc)
            state = "error"
            log_file.close()

        return {
            "camera": camera,
            "process": process,
            "log_file": log_file if process else None,
            "output_dir": output_dir,
            "state": state,
            "last_error": last_error,
            "retry_count": 0,
            "next_retry_at": None,
            "processed_files": self._seed_existing_segments(output_dir),
            "file_sizes": {},
            "last_segment_path": None,
            "last_segment_id": None,
            "last_segment_at": None,
        }

    def _restart_failed_processes(self):
        now = datetime.now().astimezone()
        with self.lock:
            for stream_key, state in list(self.processes.items()):
                process = state.get("process")
                if self._is_process_alive(process):
                    continue
                next_retry_at = state.get("next_retry_at")
                if next_retry_at and next_retry_at > now:
                    continue
                self._close_log_file(state)
                retry_count = state.get("retry_count", 0) + 1
                delay = min(60, 5 * (2 ** min(retry_count - 1, 4)))
                camera = state.get("camera") or self.cameras.get(stream_key)
                if not camera:
                    continue
                new_state = self._spawn_process(camera)
                new_state["retry_count"] = retry_count
                new_state["next_retry_at"] = now + timedelta(seconds=delay)
                if process and process.returncode not in (0, None):
                    new_state["last_error"] = f"ffmpeg exited with code {process.returncode}"
                self.processes[stream_key] = new_state

    def _scan_completed_segments(self):
        with self.lock:
            states = list(self.processes.items())
        for stream_key, state in states:
            output_dir = state.get("output_dir")
            if not output_dir or not os.path.isdir(output_dir):
                continue
            files = sorted(
                os.path.join(output_dir, name)
                for name in os.listdir(output_dir)
                if name.lower().endswith(".mp4")
            )
            for file_path in files:
                if file_path in state["processed_files"]:
                    continue
                if not self._is_file_complete(file_path, state):
                    continue
                self._persist_segment(stream_key, state, file_path)

    def _is_file_complete(self, file_path, state):
        try:
            size = os.path.getsize(file_path)
            mtime = os.path.getmtime(file_path)
        except OSError:
            return False
        if size < 1024 * 1024:
            return False
        previous = state["file_sizes"].get(file_path)
        state["file_sizes"][file_path] = size
        if previous != size:
            return False
        return time.time() - mtime >= 3

    def _persist_segment(self, stream_key, state, file_path):
        camera = state.get("camera")
        start_time = self._start_time_from_filename(file_path)
        duration = self._probe_duration(file_path) or self.segment_seconds
        end_time = start_time + timedelta(seconds=duration)
        file_size = os.path.getsize(file_path)
        segment_id = upsert_recording_segment(camera, file_path, start_time, end_time, int(round(duration)), file_size)
        state["processed_files"].add(file_path)
        state["last_segment_path"] = file_path
        state["last_segment_id"] = segment_id
        state["last_segment_at"] = datetime.now().astimezone()
        state["state"] = "recording"
        state["last_error"] = None

    def _start_time_from_filename(self, file_path):
        name = os.path.splitext(os.path.basename(file_path))[0]
        match = re.match(r"(\d{8}_\d{6})$", name)
        if match:
            parsed = datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
            return parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return datetime.fromtimestamp(os.path.getmtime(file_path), tz=datetime.now().astimezone().tzinfo)

    def _probe_duration(self, file_path):
        try:
            result = subprocess.run(
                [
                    self.ffprobe_path,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    file_path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except Exception:
            return None
        return None

    def _seed_existing_segments(self, output_dir):
        if not os.path.isdir(output_dir):
            return set()
        return {
            os.path.join(output_dir, name)
            for name in os.listdir(output_dir)
            if name.lower().endswith(".mp4")
        }

    def _is_process_alive(self, process):
        return bool(process and process.poll() is None)

    def _terminate_process(self, process):
        if not process:
            return
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=8)
        except Exception:
            try:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                process.kill()
            except Exception:
                pass

    def _close_log_file(self, state):
        log_file = state.get("log_file")
        if log_file:
            try:
                log_file.close()
            except Exception:
                pass


recording_manager = RecordingManager()
