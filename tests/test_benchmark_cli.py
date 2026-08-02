import json
import os
import subprocess
import sys
from pathlib import Path

from camera_ai.benchmark import BenchmarkCase, BenchmarkObservation
from scripts.benchmark_pipeline import (
    HttpAsyncBenchmarkClient,
    build_video_report,
    run_manifest,
    run_video_inputs,
)


class FakeAsyncClient:
    def analyze(self, case):
        return BenchmarkObservation(
            case_id=case.case_id,
            expected_event=case.expected_event,
            predicted_event=case.expected_event,
            latency_ms=125,
            time_to_first_result_ms=75,
            vlm_calls=1,
        )


def test_run_manifest_writes_jsonl_and_summary(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            [
                {
                    "case_id": "normal_01",
                    "expected_event": "normal",
                    "media_path": "normal.mp4",
                }
            ]
        ),
        encoding="utf-8",
    )
    output = tmp_path / "run"

    observations, summary = run_manifest(manifest, output, FakeAsyncClient())

    assert len(observations) == 1
    assert summary.case_count == 1
    assert json.loads((tmp_path / "run.jsonl").read_text(encoding="utf-8"))["case_id"] == "normal_01"
    assert json.loads((tmp_path / "run-summary.json").read_text(encoding="utf-8"))["case_count"] == 1


def analysis_payload(*levels):
    return {
        "status": "completed",
        "windows": [
            {
                "window_index": index,
                "start_seconds": index * 5,
                "end_seconds": (index + 1) * 5,
                "alert_level": level,
                "qwen": {
                    "status": "completed",
                    "summary": f"window {index}",
                    "degraded": False,
                    "verified": True,
                    "reason": "candidate_requires_verification",
                },
                "timing": {
                    "motion_ms": 10.0,
                    "detector_ms": 20.0,
                    "keyframe_ms": 0.1,
                    "qwen_ms": 4000.0,
                    "total_ms": 4030.1,
                    "queue_wait_ms": 25.0,
                    "wall_clock_ms": 4055.1,
                },
            }
            for index, level in enumerate(levels)
        ],
    }


def test_low_only_analysis_is_written_with_green_windows(tmp_path):
    report = build_video_report(
        tmp_path / "calm.mp4", "analysis-1", analysis_payload("low", "low")
    )

    assert report["level_summary"] == {
        "green": 2,
        "orange": 0,
        "red": 0,
        "highest_level": "low",
    }
    assert [item["alert_level"] for item in report["windows"]] == ["low", "low"]


def test_video_report_keeps_all_green_orange_and_red_windows(tmp_path):
    report = build_video_report(
        tmp_path / "event.mp4",
        "analysis-2",
        analysis_payload("low", "medium", "high"),
    )

    assert report["level_summary"] == {
        "green": 1,
        "orange": 1,
        "red": 1,
        "highest_level": "high",
    }
    assert [item["alert_level"] for item in report["windows"]] == [
        "low",
        "medium",
        "high",
    ]


def test_video_report_keeps_only_compact_public_window_fields(tmp_path):
    payload = analysis_payload("high", "high")
    payload["windows"][0]["cooldown"] = {
        "active_alert_id": "episode-1",
        "next_recheck_seconds": 65.0,
        "episode_created": True,
    }
    payload["windows"][0]["timing"]["total_ms"] = 5200.0
    payload["windows"][1]["qwen"] = {
        "status": "suppressed",
        "summary": "Inherited active alert",
        "degraded": False,
        "verified": False,
        "reason": "active_alert_cooldown",
    }
    payload["windows"][1]["cooldown"] = {
        "active_alert_id": "episode-1",
        "next_recheck_seconds": 65.0,
    }
    payload["windows"][1]["timing"] = {
        "motion_ms": 0.0,
        "detector_ms": 0.0,
        "keyframe_ms": 0.0,
        "qwen_ms": 0.0,
        "total_ms": 0.0,
        "queue_wait_ms": 25.0,
        "wall_clock_ms": 25.0,
    }

    report = build_video_report(tmp_path / "event.mp4", "analysis-3", payload)

    first, suppressed = report["windows"]
    assert set(first) == {
        "window_index",
        "start_seconds",
        "end_seconds",
        "alert_level",
        "qwen",
        "cooldown",
        "timing",
    }
    assert first["qwen"] == payload["windows"][0]["qwen"]
    assert suppressed["qwen"]["reason"] == "active_alert_cooldown"
    assert suppressed["qwen"]["verified"] is False
    assert suppressed["cooldown"]["active_alert_id"] == "episode-1"
    assert report["performance_summary"]["vlm_called_windows"] == 1
    assert report["performance_summary"]["windows_over_budget"] == 1


def test_video_report_counts_cooldown_suppression_and_rechecks(tmp_path):
    payload = analysis_payload("high", "high", "high")
    payload["windows"][0]["cooldown"] = {
        "active_alert_id": "episode-1",
        "episode_created": True,
    }
    payload["windows"][1]["qwen"] = {
        "status": "suppressed",
        "summary": "Inherited active alert",
        "degraded": False,
        "verified": False,
        "reason": "active_alert_cooldown",
    }
    payload["windows"][1]["cooldown"] = {"active_alert_id": "episode-1"}
    payload["windows"][2]["qwen"]["reason"] = "active_alert_recheck"
    payload["windows"][2]["cooldown"] = {
        "active_alert_id": "episode-1",
        "recheck": True,
        "episode_extended": True,
    }

    report = build_video_report(tmp_path / "event.mp4", "analysis-a", payload)

    summary = report["performance_summary"]
    assert summary["vlm_suppressed_by_cooldown"] == 1
    assert summary["cooldown_suppression_rate"] == 1 / 3
    assert summary["red_episodes_created"] == 1
    assert summary["red_rechecks"] == 1
    assert summary["red_cooldown_extensions"] == 1
    assert summary["failed_rechecks"] == 0
    assert report["windows"][1]["qwen"]["verified"] is False
    assert report["windows"][1]["qwen"]["reason"] == "active_alert_cooldown"
    assert report["windows"][1]["cooldown"]["active_alert_id"] == "episode-1"


class FakeVideoClient:
    def __init__(self):
        self.paths = []

    def analyze_video(self, path):
        self.paths.append(path.name)
        if path.name == "broken.mp4":
            raise RuntimeError("broken")
        return f"id-{path.stem}", analysis_payload("medium")


def test_directory_mode_filters_sorts_and_continues_after_error(tmp_path):
    for name in ["z.mov", "broken.mp4", "a.mp4", "ignore.txt"]:
        (tmp_path / name).write_bytes(b"x")
    output = tmp_path / "output"
    client = FakeVideoClient()

    result = run_video_inputs(input_dir=tmp_path, output_dir=output, client=client)

    assert client.paths == ["a.mp4", "broken.mp4", "z.mov"]
    assert result == {"processed": 2, "written": 3, "failed": 1}
    assert (output / "a.json").exists()
    failed = json.loads((output / "broken.json").read_text(encoding="utf-8"))
    assert failed["status"] == "failed"
    assert failed["error"] == "broken"
    assert (output / "z.json").exists()


def test_single_file_mode_processes_only_selected_video(tmp_path):
    selected = tmp_path / "selected.mkv"
    selected.write_bytes(b"x")
    client = FakeVideoClient()

    run_video_inputs(input_file=selected, output_dir=tmp_path / "out", client=client)

    assert client.paths == ["selected.mkv"]


def test_module_help_runs_from_repo_without_installed_package():
    repo_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, "-m", "scripts.benchmark_pipeline", "--help"],
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "--input-file" in result.stdout


def test_http_client_uses_video_stem_as_camera_id(tmp_path, monkeypatch):
    video = tmp_path / "camera_event.mp4"
    video.write_bytes(b"video")
    posted = {}

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_post(url, *, files, data, timeout):
        posted.update(data)
        return Response({"request_id": "analysis-1"})

    def fake_get(url, *, timeout):
        return Response(
            {
                "status": "completed",
                "windows": [
                    {
                        "qwen": {
                            "status": "completed",
                            "summary": "verified",
                            "degraded": False,
                        }
                    }
                ],
            }
        )

    monkeypatch.setattr("scripts.benchmark_pipeline.requests.post", fake_post)
    monkeypatch.setattr("scripts.benchmark_pipeline.requests.get", fake_get)

    client = HttpAsyncBenchmarkClient("http://api")
    analysis_id, payload = client.analyze_video(video)

    assert posted == {"camera_id": "camera_event"}
    assert analysis_id == "analysis-1"
    assert payload["status"] == "completed"
    assert client.last_first_result_ms > 0.0


def test_manifest_observation_consumes_compact_analysis_windows(monkeypatch):
    client = HttpAsyncBenchmarkClient("http://api")
    payload = analysis_payload("high", "high")
    payload["total_timing"] = {"qwen_ms": 4000.0}
    payload["windows"][1]["qwen"] = {
        "status": "suppressed",
        "summary": "Inherited active alert",
        "degraded": False,
        "verified": False,
        "reason": "active_alert_cooldown",
    }
    monkeypatch.setattr(
        client,
        "analyze_video",
        lambda path: ("analysis-1", payload),
    )
    case = BenchmarkCase(
        case_id="event-1",
        expected_event="abnormal",
        media_path="event.mp4",
    )

    observation = client.analyze(case)

    assert observation.predicted_event == "abnormal"
    assert observation.vlm_calls == 1
    assert observation.json_valid is True
