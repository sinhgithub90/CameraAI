"""Serializable contracts and aggregation for offline pipeline benchmarks."""
from __future__ import annotations

import math
from typing import Iterable, Literal

from pydantic import BaseModel, Field, JsonValue, model_validator

from .schemas import PipelineResult, StageTiming, VideoAnalysisStats, VideoWindowResult


PROCESSING_BUDGET_MS = 5000.0


def nearest_rank_percentile(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(1, math.ceil(percentile * len(ordered)))
    return float(ordered[rank - 1])


class ModelBenchmarkConfig(BaseModel):
    model: str
    frame_mode: Literal["composite", "separate"]
    keyframes: int = Field(ge=1, le=8)
    production_enabled: bool = False

    @model_validator(mode="after")
    def validate_frame_mode(self) -> "ModelBenchmarkConfig":
        if self.frame_mode == "composite" and self.keyframes != 2:
            raise ValueError("composite benchmark mode requires exactly two keyframes")
        return self


class BenchmarkCase(BaseModel):
    """Expected outcome and optional JSON-safe annotations for one media case."""

    case_id: str
    expected_event: str
    media_path: str | None = None
    annotations: dict[str, JsonValue] = Field(default_factory=dict)


class BenchmarkObservation(BaseModel):
    """Measured outcome for one benchmark case or video window."""

    case_id: str
    expected_event: str
    latency_ms: float
    vlm_calls: int
    predicted_event: str
    timing: StageTiming = Field(default_factory=StageTiming)
    queue_wait_ms: float = Field(default=0.0, ge=0.0)
    time_to_first_result_ms: float = Field(default=0.0, ge=0.0)
    json_valid: bool = True

    @classmethod
    def from_pipeline_result(
        cls,
        case: BenchmarkCase,
        result: PipelineResult,
        *,
        predicted_event: str,
    ) -> "BenchmarkObservation":
        """Create one observation from the aggregate timing already in a result."""
        stats = result.video_stats or VideoAnalysisStats()
        return cls(
            case_id=case.case_id,
            expected_event=case.expected_event,
            predicted_event=predicted_event,
            latency_ms=stats.total_ms,
            vlm_calls=stats.vlm_calls,
            timing=StageTiming(
                total_ms=stats.total_ms,
                motion_ms=stats.motion_ms,
                detector_ms=stats.detector_ms,
                qwen_ms=stats.qwen_ms,
            ),
        )

    @classmethod
    def from_video_window(
        cls,
        case: BenchmarkCase,
        window: VideoWindowResult,
        *,
        predicted_event: str,
    ) -> "BenchmarkObservation":
        """Create one observation from a single processed video window."""
        return cls(
            case_id=case.case_id,
            expected_event=case.expected_event,
            predicted_event=predicted_event,
            latency_ms=window.timing.total_ms,
            vlm_calls=0 if window.vlm.skipped else 1,
            timing=window.timing.model_copy(deep=True),
        )


class BenchmarkSummary(BaseModel):
    """Deterministic aggregate metrics for a set of benchmark observations."""

    case_count: int
    mean_latency_ms: float
    vlm_calls_total: int
    recall_by_event: dict[str, float]
    mean_timing: StageTiming
    false_positive_total: int = 0
    false_negative_total: int = 0
    json_valid_rate: float = 0.0
    mean_queue_wait_ms: float = 0.0
    mean_time_to_first_result_ms: float = 0.0
    vlm_called_windows: int = 0
    vlm_skipped_windows: int = 0
    vlm_call_rate: float = 0.0
    processing_p95_ms: float = 0.0
    windows_over_budget: int = 0


def summarize_benchmark(
    observations: Iterable[BenchmarkObservation],
) -> BenchmarkSummary:
    """Aggregate benchmark observations without executing the pipeline."""
    items = list(observations)
    count = len(items)
    if not count:
        return BenchmarkSummary(
            case_count=0,
            mean_latency_ms=0.0,
            vlm_calls_total=0,
            recall_by_event={},
            mean_timing=StageTiming(),
        )

    expected_counts: dict[str, int] = {}
    true_positive_counts: dict[str, int] = {}
    for item in items:
        expected_counts[item.expected_event] = expected_counts.get(item.expected_event, 0) + 1
        if item.predicted_event == item.expected_event:
            true_positive_counts[item.expected_event] = (
                true_positive_counts.get(item.expected_event, 0) + 1
            )

    called_windows = sum(item.vlm_calls > 0 for item in items)
    skipped_windows = count - called_windows

    return BenchmarkSummary(
        case_count=count,
        mean_latency_ms=sum(item.latency_ms for item in items) / count,
        vlm_calls_total=sum(item.vlm_calls for item in items),
        recall_by_event={
            event: true_positive_counts.get(event, 0) / expected_count
            for event, expected_count in sorted(expected_counts.items())
        },
        false_positive_total=sum(
            item.expected_event == "normal" and item.predicted_event != "normal"
            for item in items
        ),
        false_negative_total=sum(
            item.expected_event != "normal" and item.predicted_event == "normal"
            for item in items
        ),
        json_valid_rate=sum(item.json_valid for item in items) / count,
        mean_queue_wait_ms=sum(item.queue_wait_ms for item in items) / count,
        mean_time_to_first_result_ms=sum(
            item.time_to_first_result_ms for item in items
        ) / count,
        vlm_called_windows=called_windows,
        vlm_skipped_windows=skipped_windows,
        vlm_call_rate=called_windows / count,
        processing_p95_ms=nearest_rank_percentile(
            (item.latency_ms for item in items), 0.95
        ),
        windows_over_budget=sum(
            item.latency_ms > PROCESSING_BUDGET_MS for item in items
        ),
        mean_timing=StageTiming(
            total_ms=sum(item.timing.total_ms for item in items) / count,
            motion_ms=sum(item.timing.motion_ms for item in items) / count,
            detector_ms=sum(item.timing.detector_ms for item in items) / count,
            keyframe_ms=sum(item.timing.keyframe_ms for item in items) / count,
            qwen_ms=sum(item.timing.qwen_ms for item in items) / count,
            queue_wait_ms=sum(item.timing.queue_wait_ms for item in items) / count,
            wall_clock_ms=sum(item.timing.wall_clock_ms for item in items) / count,
        ),
    )
