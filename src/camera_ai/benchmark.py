"""Serializable contracts and aggregation for offline pipeline benchmarks."""
from __future__ import annotations

from typing import Iterable

from pydantic import BaseModel, Field, JsonValue

from .schemas import PipelineResult, StageTiming, VideoAnalysisStats, VideoWindowResult


class BenchmarkCase(BaseModel):
    """Expected outcome and optional JSON-safe annotations for one media case."""

    case_id: str
    expected_event: str
    annotations: dict[str, JsonValue] = Field(default_factory=dict)


class BenchmarkObservation(BaseModel):
    """Measured outcome for one benchmark case or video window."""

    case_id: str
    expected_event: str
    latency_ms: float
    vlm_calls: int
    predicted_event: str
    timing: StageTiming = Field(default_factory=StageTiming)

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

    return BenchmarkSummary(
        case_count=count,
        mean_latency_ms=sum(item.latency_ms for item in items) / count,
        vlm_calls_total=sum(item.vlm_calls for item in items),
        recall_by_event={
            event: true_positive_counts.get(event, 0) / expected_count
            for event, expected_count in sorted(expected_counts.items())
        },
        mean_timing=StageTiming(
            total_ms=sum(item.timing.total_ms for item in items) / count,
            motion_ms=sum(item.timing.motion_ms for item in items) / count,
            detector_ms=sum(item.timing.detector_ms for item in items) / count,
            qwen_ms=sum(item.timing.qwen_ms for item in items) / count,
        ),
    )
