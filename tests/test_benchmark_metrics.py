import json

import pytest
from pydantic import ValidationError

from camera_ai.benchmark import (
    BenchmarkCase,
    BenchmarkObservation,
    summarize_benchmark,
)
from camera_ai.schemas import (
    AlertLevel,
    PipelineResult,
    QwenInputSummary,
    SecurityDecision,
    StageTiming,
    VLMResult,
    VideoAnalysisStats,
    VideoWindowResult,
)


def test_benchmark_summary_aggregates_latency_and_vlm_calls():
    summary = summarize_benchmark(
        [
            BenchmarkObservation(
                case_id="normal_01",
                expected_event="normal",
                latency_ms=100,
                vlm_calls=0,
                predicted_event="normal",
            ),
            BenchmarkObservation(
                case_id="fall_01",
                expected_event="fall",
                latency_ms=300,
                vlm_calls=1,
                predicted_event="fall",
            ),
        ]
    )

    assert summary.case_count == 2
    assert summary.mean_latency_ms == 200
    assert summary.recall_by_event["fall"] == 1.0
    assert summary.vlm_calls_total == 1
    assert summary.vlm_called_windows == 1
    assert summary.vlm_skipped_windows == 1
    assert summary.vlm_call_rate == 0.5
    assert summary.processing_p95_ms == 300
    assert summary.windows_over_budget == 0


def test_benchmark_summary_reports_windows_over_five_second_budget():
    summary = summarize_benchmark(
        [
            BenchmarkObservation(
                case_id="static",
                expected_event="normal",
                predicted_event="normal",
                latency_ms=100,
                vlm_calls=0,
            ),
            BenchmarkObservation(
                case_id="slow",
                expected_event="incident",
                predicted_event="incident",
                latency_ms=5200,
                vlm_calls=1,
            ),
        ]
    )

    assert summary.processing_p95_ms == 5200
    assert summary.windows_over_budget == 1


def test_summary_reports_mean_stage_timing_and_serializes_to_json():
    summary = summarize_benchmark(
        [
            BenchmarkObservation(
                case_id="fall_01",
                expected_event="fall",
                predicted_event="fall",
                latency_ms=120,
                vlm_calls=1,
                timing=StageTiming(
                    total_ms=120,
                    motion_ms=20,
                    detector_ms=30,
                    qwen_ms=70,
                ),
            ),
            BenchmarkObservation(
                case_id="fall_02",
                expected_event="fall",
                predicted_event="normal",
                latency_ms=80,
                vlm_calls=0,
                timing=StageTiming(
                    total_ms=80,
                    motion_ms=10,
                    detector_ms=20,
                    qwen_ms=50,
                ),
            ),
        ]
    )

    assert summary.mean_timing.motion_ms == 15
    assert summary.mean_timing.detector_ms == 25
    assert summary.mean_timing.qwen_ms == 60
    assert summary.recall_by_event == {"fall": 0.5}
    assert json.loads(summary.model_dump_json())["mean_timing"] == {
        "total_ms": 100.0,
        "motion_ms": 15.0,
        "detector_ms": 25.0,
        "keyframe_ms": 0.0,
        "qwen_ms": 60.0,
        "queue_wait_ms": 0.0,
        "wall_clock_ms": 0.0,
    }


def test_empty_benchmark_summary_has_deterministic_zero_values():
    summary = summarize_benchmark([])

    assert summary.case_count == 0
    assert summary.mean_latency_ms == 0
    assert summary.vlm_calls_total == 0
    assert summary.recall_by_event == {}
    assert summary.mean_timing == StageTiming()


def test_benchmark_case_annotations_reject_non_json_values():
    with pytest.raises(ValidationError):
        BenchmarkCase(
            case_id="normal_01",
            expected_event="normal",
            annotations={"unsupported": object()},
        )


def test_observation_from_pipeline_result_uses_existing_video_statistics():
    case = BenchmarkCase(case_id="fall_01", expected_event="fall")
    result = PipelineResult(
        media_type="video",
        vlm=VLMResult(summary=""),
        security=SecurityDecision(alert_level=AlertLevel.LOW),
        video_stats=VideoAnalysisStats(
            total_ms=250,
            motion_ms=25,
            detector_ms=50,
            qwen_ms=175,
            vlm_calls=2,
        ),
    )

    observation = BenchmarkObservation.from_pipeline_result(
        case, result, predicted_event="fall"
    )

    assert observation.case_id == "fall_01"
    assert observation.expected_event == "fall"
    assert observation.predicted_event == "fall"
    assert observation.latency_ms == 250
    assert observation.vlm_calls == 2
    assert observation.timing == StageTiming(
        total_ms=250, motion_ms=25, detector_ms=50, qwen_ms=175
    )


def test_observation_from_video_window_uses_window_timing_and_vlm_status():
    case = BenchmarkCase(case_id="normal_01", expected_event="normal")
    window = VideoWindowResult(
        window_index=0,
        start_seconds=0,
        end_seconds=5,
        vlm=VLMResult(summary="", skipped=True, status="skipped"),
        security=SecurityDecision(),
        qwen_input=QwenInputSummary(),
        timing=StageTiming(total_ms=30, motion_ms=10, detector_ms=20, qwen_ms=0),
    )

    observation = BenchmarkObservation.from_video_window(
        case, window, predicted_event="normal"
    )

    assert observation.latency_ms == 30
    assert observation.vlm_calls == 0
    assert observation.timing == window.timing


def test_summary_tracks_async_quality_and_wait_metrics():
    summary = summarize_benchmark(
        [
            BenchmarkObservation(
                case_id="normal_fp",
                expected_event="normal",
                predicted_event="fire_smoke",
                latency_ms=500,
                queue_wait_ms=50,
                time_to_first_result_ms=120,
                vlm_calls=1,
                json_valid=False,
                timing=StageTiming(keyframe_ms=10),
            ),
            BenchmarkObservation(
                case_id="fall_fn",
                expected_event="fall",
                predicted_event="normal",
                latency_ms=300,
                queue_wait_ms=30,
                time_to_first_result_ms=80,
                vlm_calls=1,
                json_valid=True,
                timing=StageTiming(keyframe_ms=6),
            ),
        ]
    )

    assert summary.false_positive_total == 1
    assert summary.false_negative_total == 1
    assert summary.json_valid_rate == 0.5
    assert summary.mean_queue_wait_ms == 40
    assert summary.mean_time_to_first_result_ms == 100
    assert summary.mean_timing.keyframe_ms == 8
