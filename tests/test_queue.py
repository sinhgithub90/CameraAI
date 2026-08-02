# tests/test_queue.py
"""Tests for VLMQueue and VLMWorker."""
import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from camera_ai.queue import VLMTask, VLMQueue, VLMWorker
from camera_ai.alert_cooldown import (
    AlertRuntimePhase,
    VerificationStatus,
    WindowAlertContext,
)
from camera_ai.schemas import (
    AlertLevel,
    Detection,
    QwenInputSummary,
    SceneAnalysis,
    StageTiming,
    VideoWindowObservation,
)
from camera_ai.video_windows import ProcessedVideoWindow, RawVideoWindow
from camera_ai.vlm_policy import VLMCallDecision, VLMCallReason


def _make_task(
    task_id: str = "t1",
    priority: int = 3,
    camera_id: str = "cam_01",
    enqueued_at: float | None = None,
) -> VLMTask:
    frame = np.zeros((64, 64, 3), dtype=np.uint8)
    return VLMTask(
        task_id=task_id,
        camera_id=camera_id,
        alert_id=f"alert-{task_id}",
        frames=[frame],
        detections=[Detection(label="person", confidence=0.9, bbox=[1, 2, 3, 4])],
        rule_id="test_rule",
        priority=priority,
        enqueued_at=enqueued_at if enqueued_at is not None else time.monotonic(),
        max_keyframes=2,
    )


class TestVLMTask:
    def test_task_creation(self):
        frame = np.zeros((64, 64, 3), dtype=np.uint8)
        task = VLMTask(
            task_id="t1",
            camera_id="cam_01",
            alert_id="alert-1",
            frames=[frame],
            detections=[],
            rule_id="intrusion",
            priority=2,
            enqueued_at=100.0,
            max_keyframes=4,
        )
        assert task.task_id == "t1"
        assert task.priority == 2
        assert len(task.frames) == 1
        assert task.max_keyframes == 4

    def test_task_ordering(self):
        """Lower priority number = higher urgency for PriorityQueue."""
        t1 = _make_task("high", priority=1, enqueued_at=100.0)
        t2 = _make_task("low", priority=4, enqueued_at=100.0)
        assert t1 < t2  # priority 1 < 4
        assert t2 > t1

    def test_tiebreaker_by_time(self):
        """Same priority → earlier enqueued wins."""
        t1 = _make_task("first", priority=2, enqueued_at=100.0)
        t2 = _make_task("second", priority=2, enqueued_at=200.0)
        assert t1 < t2


class TestVLMQueue:
    @pytest.fixture
    def queue(self):
        return VLMQueue()

    @pytest.mark.asyncio
    async def test_enqueue_dequeue_order(self, queue):
        t1 = _make_task("t1", priority=4)
        t2 = _make_task("t2", priority=1)  # higher urgency
        t3 = _make_task("t3", priority=2)

        await queue.enqueue(t1)
        await queue.enqueue(t2)
        await queue.enqueue(t3)

        assert await queue.dequeue() == t2  # priority 1 first
        assert await queue.dequeue() == t3  # priority 2 second
        assert await queue.dequeue() == t1  # priority 4 last

    @pytest.mark.asyncio
    async def test_depth(self, queue):
        assert queue.depth == 0
        await queue.enqueue(_make_task("t1"))
        assert queue.depth == 1
        await queue.enqueue(_make_task("t2"))
        assert queue.depth == 2
        await queue.dequeue()
        assert queue.depth == 1

    @pytest.mark.asyncio
    async def test_dynamic_priority_aging(self, queue):
        """Task waiting > threshold gets effective priority boost at dequeue."""
        old_time = 50.0  # simulate enqueued 80s ago
        task = _make_task("old", priority=3, enqueued_at=old_time)

        # Patch time.monotonic so aging sees an 80s wait
        now = old_time + 80.0
        original_monotonic = time.monotonic
        time.monotonic = lambda: now
        try:
            await queue.enqueue(task)
            dequeued = await queue.dequeue()
            # priority 3 + 80s wait → effective priority = 1
            # The aged task should be dequeued even though it was enqueued last
            assert dequeued.task_id == "old"
        finally:
            time.monotonic = original_monotonic

    @pytest.mark.asyncio
    async def test_dequeue_empty_waits(self, queue):
        """Dequeue on empty queue should wait until item available."""
        async def delayed_enqueue():
            await asyncio.sleep(0.05)
            await queue.enqueue(_make_task("delayed"))

        start = time.monotonic()
        enqueue_task = asyncio.create_task(delayed_enqueue())
        result = await queue.dequeue()
        elapsed = time.monotonic() - start

        await enqueue_task
        assert result.task_id == "delayed"
        assert elapsed >= 0.04  # waited for enqueue

    @pytest.mark.asyncio
    async def test_remove_where_prunes_only_matching_camera_backlog(self, queue):
        """Catches pruning another camera or leaving removed work in queue depth."""
        stale = _make_task("a-stale", priority=2, camera_id="cam-a")
        stale.analysis_id = "analysis-a"
        stale.raw_window = RawVideoWindow(
            window_index=1, start_seconds=5.0, observations=[]
        )
        after_deadline = _make_task("a-later", priority=3, camera_id="cam-a")
        after_deadline.analysis_id = "analysis-a"
        after_deadline.raw_window = RawVideoWindow(
            window_index=13, start_seconds=65.0, observations=[]
        )
        other_camera = _make_task("b-stale", priority=1, camera_id="cam-b")
        other_camera.analysis_id = "analysis-b"
        other_camera.raw_window = RawVideoWindow(
            window_index=1, start_seconds=5.0, observations=[]
        )
        for task in (stale, after_deadline, other_camera):
            await queue.enqueue(task)

        removed = await queue.remove_where(
            lambda task: task.analysis_id == "analysis-a"
            and task.camera_id == "cam-a"
            and task.raw_window is not None
            and task.raw_window.start_seconds < 65.0
        )

        assert [task.task_id for task in removed] == ["a-stale"]
        assert queue.depth == 2
        assert (await queue.dequeue()).task_id == "b-stale"
        assert (await queue.dequeue()).task_id == "a-later"


class TestVLMWorker:
    @pytest.mark.asyncio
    async def test_video_task_uses_analysis_as_stream_and_persists_episode_context(self):
        pipeline = MagicMock()
        processed = ProcessedVideoWindow(
            scene=SceneAnalysis(summary="red", alert_level=AlertLevel.HIGH),
            qwen_input=QwenInputSummary(frame_count=1),
            timing=StageTiming(qwen_ms=12.5),
            observation=VideoWindowObservation(
                camera_id="cam_01",
                window_id="cam_01_000000",
                start_ms=0,
                end_ms=5000,
            ),
            vlm_call=VLMCallDecision(
                call_vlm=True,
                reason=VLMCallReason.CANDIDATE_REQUIRES_VERIFICATION,
            ),
            alert_context=WindowAlertContext(
                stream_id="analysis-1",
                camera_id="cam_01",
                state=AlertRuntimePhase.ALERT_ACTIVE,
                detected_level=AlertLevel.HIGH,
                effective_level=AlertLevel.HIGH,
                verification_status=VerificationStatus.VERIFIED,
                source="window_verification",
                window_start_seconds=0.0,
                window_end_seconds=5.0,
                active_alert_id="episode-1",
                event_type="intrusion",
                episode_created=True,
                next_recheck_event_seconds=65.0,
            ),
        )
        pipeline.process_video_window.return_value = processed
        alert_store = AsyncMock()
        analysis_store = AsyncMock()
        worker = VLMWorker(
            queue=VLMQueue(),
            pipeline=pipeline,
            alert_store=alert_store,
            event_bus=AsyncMock(),
            analysis_store=analysis_store,
        )
        raw_window = RawVideoWindow(window_index=0, start_seconds=0.0)
        task = VLMTask(
            priority=1,
            enqueued_at=time.monotonic(),
            camera_id="cam_01",
            alert_id="window-alert-1",
            analysis_id="analysis-1",
            window_index=0,
            raw_window=raw_window,
        )

        await worker._process_task(task)

        pipeline.process_video_window.assert_called_once_with(
            raw_window, "cam_01", stream_id="analysis-1"
        )
        alert_store.update_vlm.assert_awaited_once_with(
            "window-alert-1", processed.scene, qwen_ms=12.5
        )
        alert_store.apply_episode_context.assert_awaited_once_with(
            processed.alert_context
        )
        analysis_store.complete_processed_window.assert_awaited_once_with(
            "analysis-1", "window-alert-1", processed, 12.5
        )

    @pytest.mark.asyncio
    async def test_worker_processes_task(self):
        """Worker dequeues task, calls pipeline.analyze_vlm, updates alert."""
        queue = VLMQueue()
        mock_alert_store = AsyncMock()
        mock_pipeline = MagicMock()
        mock_event_bus = AsyncMock()

        analysis = SceneAnalysis(
            summary="Phân tích xong",
            alert_level=AlertLevel.MEDIUM,
        )
        mock_pipeline.analyze_vlm = MagicMock(return_value=analysis)

        worker = VLMWorker(
            queue=queue,
            pipeline=mock_pipeline,
            alert_store=mock_alert_store,
            event_bus=mock_event_bus,
        )

        # Start worker in background
        await worker.start()

        # Enqueue a task
        task = _make_task("worker-test")
        await queue.enqueue(task)

        # Wait for worker to process
        await asyncio.sleep(0.1)

        # Stop worker
        await worker.stop()

        # Verify
        mock_pipeline.analyze_vlm.assert_called_once_with(task)
        mock_alert_store.update_vlm.assert_awaited_once()
        args, kwargs = mock_alert_store.update_vlm.call_args
        assert args == ("alert-worker-test", analysis)
        assert kwargs["qwen_ms"] >= 0

    @pytest.mark.asyncio
    async def test_worker_updates_owning_video_analysis_window(self):
        queue = VLMQueue()
        mock_alert_store = AsyncMock()
        mock_analysis_store = AsyncMock()
        mock_pipeline = MagicMock()
        mock_event_bus = AsyncMock()
        scene = SceneAnalysis(summary="window done", alert_level=AlertLevel.LOW)
        mock_pipeline.analyze_vlm = MagicMock(return_value=scene)

        worker = VLMWorker(
            queue=queue,
            pipeline=mock_pipeline,
            alert_store=mock_alert_store,
            analysis_store=mock_analysis_store,
            event_bus=mock_event_bus,
        )
        await worker.start()
        task = _make_task("video-0")
        task.analysis_id = "analysis-1"
        task.window_index = 0
        await queue.enqueue(task)
        await asyncio.sleep(0.1)
        await worker.stop()

        mock_analysis_store.complete_window.assert_awaited_once()
        args, kwargs = mock_analysis_store.complete_window.call_args
        assert args[:3] == ("analysis-1", "alert-video-0", scene)
        assert kwargs["qwen_ms"] >= 0

    @pytest.mark.asyncio
    async def test_worker_handles_pipeline_error(self):
        """Worker should not crash on pipeline error, just log and continue."""
        queue = VLMQueue()
        mock_alert_store = AsyncMock()
        mock_pipeline = MagicMock()
        mock_event_bus = AsyncMock()

        mock_pipeline.analyze_vlm = MagicMock(side_effect=RuntimeError("VLM crash"))

        worker = VLMWorker(
            queue=queue,
            pipeline=mock_pipeline,
            alert_store=mock_alert_store,
            event_bus=mock_event_bus,
        )

        await worker.start()
        await queue.enqueue(_make_task("error-task"))
        await asyncio.sleep(0.1)

        await worker.stop()

        # Alert store should NOT have been updated (pipeline failed)
        mock_alert_store.update_vlm.assert_not_called()
        # Worker should still be alive (not crashed)
