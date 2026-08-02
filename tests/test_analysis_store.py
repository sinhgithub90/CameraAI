import pytest

from camera_ai.alert_cooldown import (
    AlertRuntimePhase,
    VerificationStatus,
    WindowAdmission,
    WindowAlertContext,
    WindowDisposition,
)
from camera_ai.analysis_store import (
    CompactVideoAnalysis,
    InMemoryAnalysisStore,
    VideoAnalysis,
)
from camera_ai.schemas import (
    AlertLevel,
    QwenInputSummary,
    SceneAnalysis,
    SecurityDecision,
    StageTiming,
    VideoWindowObservation,
    VideoWindowResult,
    VLMResult,
)
from camera_ai.video_windows import ProcessedVideoWindow
from camera_ai.vlm_policy import VLMCallDecision, VLMCallReason


def pending_window(alert_id: str) -> VideoWindowResult:
    return VideoWindowResult(
        alert_id=alert_id,
        window_index=0,
        start_seconds=0,
        end_seconds=5,
        vlm=VLMResult(summary="", status="pending"),
        security=SecurityDecision(alert_level=AlertLevel.LOW),
        qwen_input=QwenInputSummary(frame_count=2),
        timing=StageTiming(motion_ms=12.0, detector_ms=34.0, total_ms=46.0),
    )


def processed_window(
    level: AlertLevel, context: WindowAlertContext
) -> ProcessedVideoWindow:
    return ProcessedVideoWindow(
        scene=SceneAnalysis(alert_level=level),
        qwen_input=QwenInputSummary(frame_count=2),
        timing=StageTiming(qwen_ms=10.0, total_ms=10.0),
        observation=VideoWindowObservation(
            camera_id=context.camera_id,
            window_id=f"{context.camera_id}_{int(context.window_start_seconds // 5):06d}",
            start_ms=round(context.window_start_seconds * 1000),
            end_ms=round(context.window_end_seconds * 1000),
        ),
        vlm_call=VLMCallDecision(
            call_vlm=True,
            reason=VLMCallReason.ACTIVE_ALERT_RECHECK,
        ),
        alert_context=context,
    )


@pytest.mark.asyncio
async def test_analysis_store_updates_window_and_aggregate_timing():
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-1", camera_id="cam_01"))
    await store.append_window("analysis-1", pending_window("alert-1"))
    await store.complete_window(
        "analysis-1",
        "alert-1",
        SceneAnalysis(summary="done", alert_level=AlertLevel.MEDIUM),
        qwen_ms=56.0,
    )

    analysis = await store.get("analysis-1")
    assert analysis is not None
    assert analysis.windows[0].vlm.status == "completed"
    assert analysis.windows[0].timing.qwen_ms == 56.0
    assert analysis.total_timing.total_ms == 102.0


@pytest.mark.asyncio
async def test_analysis_store_marks_completed_only_after_producer_finishes():
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-2", camera_id="cam_01"))
    await store.append_window("analysis-2", pending_window("alert-2"))
    await store.mark_producer_complete("analysis-2")

    queued = await store.get("analysis-2")
    assert queued is not None
    assert queued.status == "queued"

    await store.complete_window(
        "analysis-2",
        "alert-2",
        SceneAnalysis(summary="done", alert_level=AlertLevel.LOW),
        qwen_ms=10.0,
    )
    completed = await store.get("analysis-2")
    assert completed is not None
    assert completed.status == "completed"


@pytest.mark.asyncio
async def test_analysis_store_exposes_producer_failure():
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-3", camera_id="cam_01"))

    await store.mark_producer_failed("analysis-3", "cannot read video")

    analysis = await store.get("analysis-3")
    assert analysis is not None
    assert analysis.status == "failed"
    assert analysis.error == "cannot read video"


@pytest.mark.asyncio
async def test_has_active_camera_only_matches_reading_or_queued_analyses():
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="reading", camera_id="cam-active"))
    await store.create(
        VideoAnalysis(id="completed", camera_id="cam-done", status="completed")
    )
    await store.create(
        VideoAnalysis(id="failed", camera_id="cam-failed", status="failed")
    )

    assert await store.has_active_camera("cam-active") is True
    assert await store.has_active_camera("cam-done") is False
    assert await store.has_active_camera("cam-failed") is False
    assert await store.has_active_camera("unknown") is False


@pytest.mark.asyncio
async def test_processed_static_window_is_persisted_as_skipped_and_completes():
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-4", camera_id="cam_01"))
    await store.append_window("analysis-4", pending_window("alert-4"))
    await store.mark_producer_complete("analysis-4")
    processed = ProcessedVideoWindow(
        scene=SceneAnalysis(summary="static", alert_level=AlertLevel.LOW),
        qwen_input=QwenInputSummary(frame_count=2),
        timing=StageTiming(total_ms=10.0, qwen_ms=0.0),
        observation=VideoWindowObservation(
            camera_id="cam_01",
            window_id="cam_01_000000",
            start_ms=0,
            end_ms=5000,
        ),
        vlm_call=VLMCallDecision(
            call_vlm=False,
            reason=VLMCallReason.STATIC_WINDOW,
        ),
    )

    await store.complete_processed_window("analysis-4", "alert-4", processed, 0.0)

    analysis = await store.get("analysis-4")
    assert analysis is not None
    saved = analysis.windows[0]
    assert saved.vlm.status == "skipped"
    assert saved.vlm.skipped is True
    assert saved.event_metadata["vlm_call"] == {
        "call_vlm": False,
        "reason": "static_window",
        "priority": "low",
        "candidate_id": None,
    }
    assert saved.timing.qwen_ms == 0
    assert analysis.status == "completed"


@pytest.mark.asyncio
async def test_suppressed_window_persists_inherited_alert_context():
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-a", camera_id="cam-a"))
    await store.append_window("analysis-a", pending_window("window-2"))
    await store.mark_producer_complete("analysis-a")
    processed = ProcessedVideoWindow(
        scene=SceneAnalysis(summary="inherited", alert_level=AlertLevel.HIGH),
        qwen_input=QwenInputSummary(),
        timing=StageTiming(),
        observation=VideoWindowObservation(
            camera_id="cam-a",
            window_id="cam-a_000001",
            start_ms=5000,
            end_ms=10000,
        ),
        vlm_call=VLMCallDecision(
            call_vlm=False,
            reason=VLMCallReason.ACTIVE_ALERT_COOLDOWN,
        ),
        alert_context=WindowAlertContext(
            stream_id="analysis-a",
            camera_id="cam-a",
            state=AlertRuntimePhase.ALERT_ACTIVE,
            effective_level=AlertLevel.HIGH,
            verification_status=VerificationStatus.SUPPRESSED,
            source="inherited_active_alert",
            window_start_seconds=5.0,
            window_end_seconds=10.0,
            active_alert_id="episode-1",
            event_type="traffic_accident",
            next_recheck_event_seconds=65.0,
        ),
    )

    await store.complete_processed_window(
        "analysis-a", "window-2", processed, qwen_ms=0.0
    )

    saved = (await store.get("analysis-a")).windows[0]
    assert saved.vlm.status == "suppressed"
    assert saved.vlm.skipped is True
    assert saved.security.alert_level is AlertLevel.HIGH
    assert (await store.get("analysis-a")).status == "completed"
    assert saved.timing.total_ms == 0
    assert saved.timing.motion_ms == 0
    assert saved.timing.detector_ms == 0
    assert saved.timing.keyframe_ms == 0
    assert saved.timing.qwen_ms == 0
    assert saved.event_metadata["alert_context"]["verification_status"] == "suppressed"
    assert saved.event_metadata["alert_context"]["active_alert_id"] == "episode-1"


@pytest.mark.asyncio
async def test_prequeue_drop_updates_one_compact_summary_without_window_record():
    """Catches dropped cooldown work leaking back into the public windows list."""
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-red", camera_id="cam-a"))
    admission = WindowAdmission(
        stream_id="analysis-red",
        camera_id="cam-a",
        start_seconds=5.0,
        end_seconds=10.0,
        disposition=WindowDisposition.SUPPRESS,
        process_window=False,
        state=AlertRuntimePhase.ALERT_ACTIVE,
        effective_level=AlertLevel.HIGH,
        active_alert_id="episode-1",
        next_recheck_event_seconds=65.0,
    )

    await store.record_suppressed_window(
        "analysis-red", admission, timebase="video"
    )

    analysis = await store.get("analysis-red")
    assert analysis is not None
    assert analysis.windows == []
    assert analysis.cooldown is not None
    assert analysis.cooldown.suppressed_windows == 1
    assert analysis.cooldown.suppressed_seconds == 5.0
    assert analysis.cooldown.red_started == 5.0
    compact = CompactVideoAnalysis.from_analysis(analysis)
    assert compact.windows == []
    assert compact.cooldown == analysis.cooldown


@pytest.mark.asyncio
async def test_remove_pending_windows_counts_only_removed_source_durations():
    """Catches pruned jobs remaining visible or completed work being deleted."""
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-prune", camera_id="cam-a"))
    stale = pending_window("stale")
    survivor = pending_window("survivor").model_copy(
        update={"window_index": 1, "start_seconds": 5.0, "end_seconds": 10.0}
    )
    await store.append_window("analysis-prune", stale)
    await store.append_window("analysis-prune", survivor)
    context = WindowAlertContext(
        stream_id="analysis-prune",
        camera_id="cam-a",
        state=AlertRuntimePhase.ALERT_ACTIVE,
        effective_level=AlertLevel.HIGH,
        active_alert_id="episode-1",
        window_end_seconds=5.0,
        episode_created=True,
        next_recheck_event_seconds=65.0,
    )

    removed = await store.remove_pending_windows(
        "analysis-prune", {"stale"}, context=context, timebase="video"
    )

    analysis = await store.get("analysis-prune")
    assert removed == ["stale"]
    assert [window.alert_id for window in analysis.windows] == ["survivor"]
    assert analysis.cooldown.suppressed_windows == 1
    assert analysis.cooldown.suppressed_seconds == 5.0


@pytest.mark.asyncio
async def test_red_result_exposes_recheck_signal_before_any_window_is_dropped():
    """Catches clients learning cooldown state only after a later suppression."""
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-signal", camera_id="cam-a"))
    await store.append_window("analysis-signal", pending_window("red"))
    processed = ProcessedVideoWindow(
        scene=SceneAnalysis(alert_level=AlertLevel.HIGH),
        qwen_input=QwenInputSummary(frame_count=2),
        timing=StageTiming(qwen_ms=10.0, total_ms=10.0),
        observation=VideoWindowObservation(
            camera_id="cam-a", window_id="cam-a_000000", start_ms=0, end_ms=5000
        ),
        vlm_call=VLMCallDecision(
            call_vlm=True,
            reason=VLMCallReason.CANDIDATE_REQUIRES_VERIFICATION,
        ),
        alert_context=WindowAlertContext(
            stream_id="analysis-signal",
            camera_id="cam-a",
            state=AlertRuntimePhase.ALERT_ACTIVE,
            effective_level=AlertLevel.HIGH,
            active_alert_id="episode-1",
            window_end_seconds=5.0,
            episode_created=True,
            next_recheck_event_seconds=65.0,
        ),
    )

    await store.complete_processed_window("analysis-signal", "red", processed, 10.0)

    cooldown = (await store.get("analysis-signal")).cooldown
    assert cooldown.active_alert_id == "episode-1"
    assert cooldown.red_started == 5.0
    assert cooldown.recheck_at == 65.0
    assert cooldown.suppressed_windows == 0


@pytest.mark.asyncio
async def test_discard_pending_window_removes_late_rejected_queue_record():
    """Catches a queue cutoff rejection leaving an analysis stuck pending."""
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-late", camera_id="cam-a"))
    await store.append_window("analysis-late", pending_window("late"))

    removed = await store.discard_pending_window("analysis-late", "late")

    analysis = await store.get("analysis-late")
    assert removed is True
    assert analysis.windows == []
    assert analysis.status == "reading"


@pytest.mark.asyncio
async def test_medium_recheck_refreshes_current_cooldown_level_and_deadline():
    """Catches high cooldown metadata surviving after runtime enters orange watch."""
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-medium", camera_id="cam-a"))
    await store.append_window("analysis-medium", pending_window("red"))
    created = WindowAlertContext(
        stream_id="analysis-medium",
        camera_id="cam-a",
        state=AlertRuntimePhase.ALERT_ACTIVE,
        effective_level=AlertLevel.HIGH,
        active_alert_id="episode-1",
        window_end_seconds=5.0,
        episode_created=True,
        next_recheck_event_seconds=65.0,
    )
    await store.complete_processed_window(
        "analysis-medium", "red", processed_window(AlertLevel.HIGH, created), 10.0
    )
    medium_pending = pending_window("medium").model_copy(
        update={"window_index": 13, "start_seconds": 65.0, "end_seconds": 70.0}
    )
    await store.append_window("analysis-medium", medium_pending)
    medium = created.model_copy(
        update={
            "state": AlertRuntimePhase.ORANGE_WATCH,
            "effective_level": AlertLevel.MEDIUM,
            "window_start_seconds": 65.0,
            "window_end_seconds": 70.0,
            "episode_created": False,
            "recheck": True,
            "next_recheck_event_seconds": 85.0,
        }
    )

    await store.complete_processed_window(
        "analysis-medium", "medium", processed_window(AlertLevel.MEDIUM, medium), 10.0
    )

    cooldown = (await store.get("analysis-medium")).cooldown
    assert cooldown.alert_level is AlertLevel.MEDIUM
    assert cooldown.recheck_at == 85.0
    assert cooldown.red_started == 5.0


@pytest.mark.asyncio
async def test_new_red_episode_resets_red_started_timestamp():
    """Catches a resolved episode's start leaking into a later distinct red."""
    store = InMemoryAnalysisStore()
    await store.create(VideoAnalysis(id="analysis-repeat", camera_id="cam-a"))
    await store.append_window("analysis-repeat", pending_window("red-1"))
    first = WindowAlertContext(
        stream_id="analysis-repeat",
        camera_id="cam-a",
        state=AlertRuntimePhase.ALERT_ACTIVE,
        effective_level=AlertLevel.HIGH,
        active_alert_id="episode-1",
        window_end_seconds=5.0,
        episode_created=True,
        next_recheck_event_seconds=65.0,
    )
    await store.complete_processed_window(
        "analysis-repeat", "red-1", processed_window(AlertLevel.HIGH, first), 10.0
    )
    await store.append_window("analysis-repeat", pending_window("resolved"))
    resolved = first.model_copy(
        update={
            "state": AlertRuntimePhase.NORMAL,
            "effective_level": AlertLevel.LOW,
            "window_end_seconds": 70.0,
            "episode_created": False,
            "episode_resolved": True,
            "next_recheck_event_seconds": None,
        }
    )
    await store.complete_processed_window(
        "analysis-repeat", "resolved", processed_window(AlertLevel.LOW, resolved), 10.0
    )
    await store.append_window("analysis-repeat", pending_window("red-2"))
    second = first.model_copy(
        update={
            "active_alert_id": "episode-2",
            "window_end_seconds": 80.0,
            "next_recheck_event_seconds": 140.0,
        }
    )

    await store.complete_processed_window(
        "analysis-repeat", "red-2", processed_window(AlertLevel.HIGH, second), 10.0
    )

    cooldown = (await store.get("analysis-repeat")).cooldown
    assert cooldown.active_alert_id == "episode-2"
    assert cooldown.red_started == 80.0
