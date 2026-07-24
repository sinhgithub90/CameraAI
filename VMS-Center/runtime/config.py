import os
import shutil
from dataclasses import dataclass

from dotenv import load_dotenv


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
load_dotenv(os.path.join(ROOT_DIR, ".env"))


def _bool_env(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _path_env(name, default):
    value = os.getenv(name)
    return os.path.abspath(value) if value else os.path.abspath(default)


@dataclass(frozen=True)
class RuntimeConfig:
    database_url: str
    go2rtc_enabled: bool
    go2rtc_exe_path: str
    go2rtc_config_path: str
    go2rtc_api_url: str
    ffmpeg_path: str
    ffprobe_path: str
    recordings_dir: str
    recording_auto_start: bool
    health_auto_start: bool
    startup_strict_mode: bool


def load_runtime_config():
    return RuntimeConfig(
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql://postgres:trinh123@localhost:5432/camera_system_db",
        ),
        go2rtc_enabled=_bool_env("GO2RTC_ENABLED", True),
        go2rtc_exe_path=_path_env("GO2RTC_EXE_PATH", os.path.join(BASE_DIR, "go2rtc.exe")),
        go2rtc_config_path=_path_env("GO2RTC_CONFIG_PATH", os.path.join(BASE_DIR, "go2rtc.yaml")),
        go2rtc_api_url=(os.getenv("GO2RTC_API_URL") or os.getenv("GO2RTC_BASE_URL") or "http://127.0.0.1:1984").rstrip("/"),
        ffmpeg_path=os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg",
        ffprobe_path=os.getenv("FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe",
        recordings_dir=_path_env("RECORDINGS_DIR", os.path.join(ROOT_DIR, "recordings")),
        recording_auto_start=_bool_env("RECORDING_AUTO_START", True),
        health_auto_start=_bool_env("HEALTH_AUTO_START", True),
        startup_strict_mode=_bool_env("STARTUP_STRICT_MODE", False),
    )
