import json
import os
import subprocess
import sys
from pathlib import Path

from camera_ai.benchmark import BenchmarkObservation
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
                "vlm": {"summary": f"window {index}"},
                "security": {
                    "alert_level": level,
                    "risks": [f"risk-{index}"],
                    "recommended_action": "review",
                },
                "detections": [],
                "qwen_input": {"frame_count": 2},
                "timing": {"total_ms": 100},
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


def test_video_report_exposes_candidate_decision_and_raw_validity(tmp_path):
    payload = analysis_payload("medium")
    payload["windows"][0]["event_metadata"] = {
        "candidates": [{"candidate_type": "person_only_activity"}],
        "decision": {"decision": "yes"},
        "vlm_trace": {"raw_output_valid": True},
    }

    report = build_video_report(tmp_path / "event.mp4", "analysis-3", payload)

    window = report["windows"][0]
    assert window["candidates"][0]["candidate_type"] == "person_only_activity"
    assert window["decision"]["decision"] == "yes"
    assert window["raw_output_valid"] is True


def test_video_report_summarizes_vlm_calls_and_processing_budget(tmp_path):
    payload = analysis_payload("low", "medium")
    payload["windows"][0]["timing"]["total_ms"] = 100
    payload["windows"][0]["event_metadata"] = {
        "vlm_call": {"call_vlm": False, "reason": "static_window"}
    }
    payload["windows"][1]["timing"]["total_ms"] = 5200
    payload["windows"][1]["event_metadata"] = {
        "vlm_call": {
            "call_vlm": True,
            "reason": "candidate_requires_verification",
        }
    }

    report = build_video_report(tmp_path / "mixed.mp4", "analysis-4", payload)

    assert report["performance_summary"] == {
        "vlm_called_windows": 1,
        "vlm_skipped_windows": 1,
        "vlm_call_rate": 0.5,
        "processing_p95_ms": 5200.0,
        "windows_over_budget": 1,
        "processing_budget_ms": 5000.0,
    }
    assert report["windows"][0]["within_processing_budget"] is True
    assert report["windows"][1]["within_processing_budget"] is False


def test_video_report_replaces_raw_bboxes_with_detection_summary(tmp_path):
    payload = analysis_payload("low")
    payload["windows"][0]["detections"] = [
        {"label": "person", "confidence": 0.7, "bbox": [0, 0, 10, 20]},
        {"label": "person", "confidence": 0.9, "bbox": [1, 0, 11, 20]},
        {"label": "car", "confidence": 0.8, "bbox": [20, 0, 40, 20]},
    ]

    window = build_video_report(
        tmp_path / "objects.mp4", "analysis-5", payload
    )["windows"][0]

    assert "detections" not in window
    assert window["detection_summary"] == {
        "car": {"detection_count": 1, "max_confidence": 0.8},
        "person": {"detection_count": 2, "max_confidence": 0.9},
    }
    assert "bbox" not in json.dumps(window)


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
        return Response({"status": "completed", "windows": []})

    monkeypatch.setattr("scripts.benchmark_pipeline.requests.post", fake_post)
    monkeypatch.setattr("scripts.benchmark_pipeline.requests.get", fake_get)

    analysis_id, payload = HttpAsyncBenchmarkClient("http://api").analyze_video(video)

    assert posted == {"camera_id": "camera_event"}
    assert analysis_id == "analysis-1"
    assert payload["status"] == "completed"
