"""Benchmark the running FastAPI async video path from a JSON manifest."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Protocol

import requests

# This repository uses a ``src`` layout. Allow ``python -m scripts...`` from
# the checkout root even when the project has not been installed editable.
_SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if _SRC_DIR.is_dir() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from camera_ai.benchmark import (
    BenchmarkCase,
    BenchmarkObservation,
    BenchmarkSummary,
    PROCESSING_BUDGET_MS,
    nearest_rank_percentile,
    summarize_benchmark,
)
from camera_ai.schemas import StageTiming


SUPPORTED_VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv"}
_ALERT_LEVELS = {"low", "medium", "high"}


def summarize_detections(detections: list[dict]) -> dict[str, dict[str, float | int]]:
    summary: dict[str, dict[str, float | int]] = {}
    for detection in detections:
        label = str(detection.get("label", "unknown"))
        confidence = float(detection.get("confidence", 0.0))
        item = summary.setdefault(
            label,
            {"detection_count": 0, "max_confidence": 0.0},
        )
        item["detection_count"] += 1
        item["max_confidence"] = max(item["max_confidence"], confidence)
    return {label: summary[label] for label in sorted(summary)}


def build_alert_report(
    video_path: str | Path, analysis_id: str, payload: dict
) -> dict | None:
    path = Path(video_path)
    alerts = []
    for window in payload.get("windows", []):
        security = window.get("security", {})
        level = security.get("alert_level", "low")
        if level not in {"medium", "high"}:
            continue
        alerts.append(
            {
                "window_index": window.get("window_index"),
                "start_seconds": window.get("start_seconds"),
                "end_seconds": window.get("end_seconds"),
                "alert_level": level,
                "summary": window.get("vlm", {}).get("summary", ""),
                "risks": security.get("risks", []),
                "recommended_action": security.get("recommended_action", ""),
                "detection_summary": summarize_detections(
                    window.get("detections", [])
                ),
                "qwen_input": window.get("qwen_input", {}),
                "timing": window.get("timing", {}),
            }
        )
    if not alerts:
        return None
    orange = sum(item["alert_level"] == "medium" for item in alerts)
    red = sum(item["alert_level"] == "high" for item in alerts)
    return {
        "video": path.name,
        "camera_id": path.stem,
        "analysis_id": analysis_id,
        "status": payload.get("status", "completed"),
        "alert_summary": {
            "orange": orange,
            "red": red,
            "highest_level": "high" if red else "medium",
        },
        "alerts": alerts,
    }


def resolve_window_alert_level(window: dict) -> str:
    event_metadata = window.get("event_metadata", {})
    alert = event_metadata.get("alert") or {}
    security = window.get("security", {})
    level = alert.get("severity") or security.get("alert_level", "low")
    return level if level in _ALERT_LEVELS else "low"


def primary_candidate_type(event_metadata: dict) -> str | None:
    candidates = event_metadata.get("candidates") or []
    if not candidates:
        return None
    vlm_call = event_metadata.get("vlm_call") or {}
    decision = event_metadata.get("decision") or {}
    primary_id = vlm_call.get("candidate_id") or decision.get("candidate_id")
    if primary_id is not None:
        for candidate in candidates:
            if candidate.get("candidate_id") == primary_id:
                return candidate.get("candidate_type")
    return candidates[0].get("candidate_type")


def build_video_report(
    video_path: str | Path, analysis_id: str, payload: dict
) -> dict:
    path = Path(video_path)
    windows = []
    processing_times = []
    called_windows = 0
    counts = {"low": 0, "medium": 0, "high": 0}
    for window in payload.get("windows", []):
        level = resolve_window_alert_level(window)
        counts[level] += 1
        event_metadata = window.get("event_metadata", {})
        vlm_call = event_metadata.get("vlm_call")
        if vlm_call is None:
            vlm_call = {
                "call_vlm": not window.get("vlm", {}).get("skipped", False),
                "reason": "legacy_payload",
            }
        call_vlm = bool(vlm_call.get("call_vlm"))
        called_windows += call_vlm
        timing = window.get("timing", {})
        decision = event_metadata.get("decision") or {}
        qwen_input = window.get("qwen_input", {})
        total_ms = float(timing.get("total_ms", 0.0))
        processing_times.append(total_ms)
        windows.append(
            {
                "window_index": window.get("window_index"),
                "start_seconds": window.get("start_seconds"),
                "end_seconds": window.get("end_seconds"),
                "alert_level": level,
                "candidate_type": primary_candidate_type(event_metadata),
                "qwen": {
                    "called": call_vlm,
                    "decision": decision.get("decision"),
                    "event_type": decision.get("event_type"),
                    "summary": window.get("vlm", {}).get("summary", ""),
                    "timestamps_seconds": qwen_input.get(
                        "timestamps_seconds", []
                    ),
                },
                "timing": {
                    "motion_ms": float(timing.get("motion_ms", 0.0)),
                    "detector_ms": float(timing.get("detector_ms", 0.0)),
                    "keyframe_ms": float(timing.get("keyframe_ms", 0.0)),
                    "qwen_ms": float(timing.get("qwen_ms", 0.0)),
                    "total_ms": total_ms,
                    "queue_wait_ms": float(timing.get("queue_wait_ms", 0.0)),
                    "wall_clock_ms": float(timing.get("wall_clock_ms", 0.0)),
                    "within_budget": total_ms <= PROCESSING_BUDGET_MS,
                },
            }
        )
    highest = "high" if counts["high"] else "medium" if counts["medium"] else "low"
    return {
        "video": path.name,
        "camera_id": path.stem,
        "analysis_id": analysis_id,
        "status": payload.get("status", "completed"),
        "level_summary": {
            "green": counts["low"],
            "orange": counts["medium"],
            "red": counts["high"],
            "highest_level": highest,
        },
        "performance_summary": {
            "vlm_called_windows": called_windows,
            "vlm_skipped_windows": len(windows) - called_windows,
            "vlm_call_rate": called_windows / len(windows) if windows else 0.0,
            "processing_p95_ms": nearest_rank_percentile(processing_times, 0.95),
            "windows_over_budget": sum(
                value > PROCESSING_BUDGET_MS for value in processing_times
            ),
            "processing_budget_ms": PROCESSING_BUDGET_MS,
        },
        "windows": windows,
    }


def build_failed_report(video_path: str | Path, error: Exception) -> dict:
    path = Path(video_path)
    return {
        "video": path.name,
        "camera_id": path.stem,
        "analysis_id": None,
        "status": "failed",
        "error": str(error),
        "level_summary": {
            "green": 0,
            "orange": 0,
            "red": 0,
            "highest_level": None,
        },
        "windows": [],
    }


def write_alert_report(output_dir: str | Path, report: dict) -> Path:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{Path(report['video']).stem}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(target)
    return target


def run_video_inputs(
    *,
    input_file: str | Path | None = None,
    input_dir: str | Path | None = None,
    output_dir: str | Path,
    client,
) -> dict[str, int]:
    if (input_file is None) == (input_dir is None):
        raise ValueError("provide exactly one of input_file or input_dir")
    if input_file is not None:
        paths = [Path(input_file)]
    else:
        directory = Path(input_dir)
        paths = sorted(
            (
                path
                for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in SUPPORTED_VIDEO_SUFFIXES
            ),
            key=lambda path: path.name.lower(),
        )
    counters = {"processed": 0, "written": 0, "failed": 0}
    for path in paths:
        try:
            analysis_id, payload = client.analyze_video(path)
            counters["processed"] += 1
            report = build_video_report(path, analysis_id, payload)
            write_alert_report(output_dir, report)
            counters["written"] += 1
            print(f"[{payload.get('status', 'completed')}] {path.name}")
        except Exception as exc:  # keep directory batch moving
            counters["failed"] += 1
            write_alert_report(output_dir, build_failed_report(path, exc))
            counters["written"] += 1
            print(f"[failed] {path.name}: {exc}")
    return counters


class AsyncBenchmarkClient(Protocol):
    def analyze(self, case: BenchmarkCase) -> BenchmarkObservation: ...


class HttpAsyncBenchmarkClient:
    def __init__(
        self,
        base_url: str,
        poll_interval: float = 0.2,
        timeout: float = 3600.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.last_first_result_ms = 0.0

    def analyze_video(self, path: str | Path) -> tuple[str, dict]:
        path = Path(path)
        started = time.perf_counter()
        with path.open("rb") as media:
            response = requests.post(
                f"{self.base_url}/async/analyze/video",
                files={"file": (path.name, media, "video/mp4")},
                data={"camera_id": path.stem},
                timeout=120,
            )
        response.raise_for_status()
        analysis_id = response.json()["request_id"]
        payload: dict = {}
        self.last_first_result_ms = 0.0
        while True:
            poll = requests.get(
                f"{self.base_url}/analyses/{analysis_id}", timeout=30
            )
            poll.raise_for_status()
            payload = poll.json()
            if not self.last_first_result_ms and any(
                window.get("vlm", {}).get("status") == "completed"
                for window in payload.get("windows", [])
            ):
                self.last_first_result_ms = (
                    time.perf_counter() - started
                ) * 1000
            if payload.get("status") in {"completed", "failed"}:
                break
            if time.perf_counter() - started > self.timeout:
                raise TimeoutError(f"analysis timed out after {self.timeout}s")
            time.sleep(self.poll_interval)
        return analysis_id, payload

    def analyze(self, case: BenchmarkCase) -> BenchmarkObservation:
        if not case.media_path:
            raise ValueError(f"case {case.case_id} has no media_path")
        started = time.perf_counter()
        _analysis_id, payload = self.analyze_video(case.media_path)
        first_result_ms = self.last_first_result_ms
        latency_ms = (time.perf_counter() - started) * 1000
        windows = payload.get("windows", [])
        levels = [w.get("security", {}).get("alert_level", "low") for w in windows]
        predicted = "normal" if all(level == "low" for level in levels) else next(
            (
                risk
                for window in windows
                for risk in window.get("security", {}).get("risks", [])
            ),
            "abnormal",
        )
        timing = StageTiming.model_validate(payload.get("total_timing", {}))
        return BenchmarkObservation(
            case_id=case.case_id,
            expected_event=case.expected_event,
            predicted_event=predicted,
            latency_ms=latency_ms,
            time_to_first_result_ms=first_result_ms,
            queue_wait_ms=timing.queue_wait_ms,
            vlm_calls=len(windows),
            json_valid=bool(windows)
            and all(
                bool(w.get("vlm", {}).get("summary"))
                and not w.get("vlm", {}).get("degraded", False)
                for w in windows
            ),
            timing=timing,
        )


def run_manifest(
    manifest_path: str | Path,
    output_prefix: str | Path,
    client: AsyncBenchmarkClient,
) -> tuple[list[BenchmarkObservation], BenchmarkSummary]:
    raw_cases = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    cases = [BenchmarkCase.model_validate(item) for item in raw_cases]
    observations = [client.analyze(case) for case in cases]
    summary = summarize_benchmark(observations)
    prefix = Path(output_prefix)
    jsonl_path = prefix.with_suffix(".jsonl")
    summary_path = prefix.with_name(f"{prefix.name}-summary.json")
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path.write_text(
        "\n".join(item.model_dump_json() for item in observations) + "\n",
        encoding="utf-8",
    )
    summary_path.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    return observations, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--manifest")
    inputs.add_argument("--input-file")
    inputs.add_argument("--input-dir")
    parser.add_argument("--output")
    parser.add_argument("--output-dir")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--poll-interval", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=3600.0)
    parser.add_argument("--model")
    parser.add_argument("--frame-mode", choices=["composite", "separate"])
    parser.add_argument("--keyframes", type=int, default=2)
    parser.add_argument("--warmup", type=int, default=0)
    args = parser.parse_args()
    client = HttpAsyncBenchmarkClient(
        args.base_url,
        poll_interval=args.poll_interval,
        timeout=args.timeout,
    )
    if args.manifest:
        if not args.output:
            parser.error("--manifest requires --output")
        run_manifest(args.manifest, args.output, client)
    else:
        if not args.output_dir:
            parser.error("--input-file/--input-dir requires --output-dir")
        counters = run_video_inputs(
            input_file=args.input_file,
            input_dir=args.input_dir,
            output_dir=args.output_dir,
            client=client,
        )
        print(json.dumps(counters, ensure_ascii=False))


if __name__ == "__main__":
    main()
