"""VLM priority queue and background worker for async processing."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from .alert_cooldown import WindowAdmission
from .messaging.contracts import Delivery, RejectMessageError, TaskQueue
from .messaging.inprocess import InProcessTaskQueue
from .schemas import Detection, SceneAnalysis
from .video_windows import RawVideoWindow

if TYPE_CHECKING:
    from .analysis_store import AnalysisStore
    from .alert_store import AlertStore
    from .events import EventBus
    from .pipeline import SecurityAIPipeline

logger = logging.getLogger(__name__)

AGING_30S = 30.0
AGING_60S = 60.0
AGING_120S = 120.0


@dataclass(order=True)
class VLMTask:
    """One VLM analysis task in the priority queue."""

    priority: int
    enqueued_at: float
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex, compare=False)
    camera_id: str = field(default="unknown", compare=False)
    alert_id: str = field(default="", compare=False)
    analysis_id: str = field(default="", compare=False)
    window_index: int | None = field(default=None, compare=False)
    frames: list[np.ndarray] = field(default_factory=list, compare=False)
    detections: list[Detection] = field(default_factory=list, compare=False)
    rule_id: str = field(default="default", compare=False)
    max_keyframes: int = field(default=2, compare=False)
    raw_window: RawVideoWindow | None = field(default=None, compare=False)
    admission: WindowAdmission | None = field(default=None, compare=False)

    def effective_priority(self, now: float | None = None) -> int:
        """Priority with aging: tasks waiting too long get boosted."""
        if now is None:
            now = time.monotonic()
        wait = now - self.enqueued_at
        boost = 0
        if wait > AGING_120S:
            boost = 3
        elif wait > AGING_60S:
            boost = 2
        elif wait > AGING_30S:
            boost = 1
        return max(1, self.priority - boost)


class VLMQueue(InProcessTaskQueue[VLMTask]):
    """Backward-compatible in-process VLM work queue."""

    def __init__(self, *, max_queue_size: int = 1024, max_attempts: int = 3) -> None:
        super().__init__(
            max_queue_size=max_queue_size,
            max_attempts=max_attempts,
            priority_resolver=lambda task, _queued: task.effective_priority(),
        )
        self._stream_lock = asyncio.Lock()
        self._stream_cutoffs: dict[tuple[str, str], float] = {}

    async def enqueue(self, task: VLMTask, *, priority: int | None = None) -> bool:
        resolved_priority = task.priority if priority is None else priority
        async with self._stream_lock:
            cutoff = self._stream_cutoffs.get((task.analysis_id, task.camera_id))
            if (
                cutoff is not None
                and task.raw_window is not None
                and task.raw_window.start_seconds < cutoff
            ):
                return False
            await super().enqueue(task, priority=resolved_priority)
        logger.debug(
            "[vlm-queue] enqueued alert=%s priority=%s depth=%s",
            task.alert_id,
            resolved_priority,
            self.depth,
        )
        return True

    async def dequeue(self) -> VLMTask:
        """Legacy helper that auto-ACKs its delivery."""
        delivery = await self.receive()
        await delivery.ack()
        logger.debug(
            "[vlm-queue] dequeued alert=%s priority=%s depth=%s",
            delivery.task.alert_id,
            delivery.task.priority,
            self.depth,
        )
        return delivery.task

    async def suppress_stream_before(
        self, *, analysis_id: str, camera_id: str, deadline: float
    ) -> list[VLMTask]:
        """Atomically register a cutoff and remove already queued stale work."""
        async with self._stream_lock:
            key = (analysis_id, camera_id)
            self._stream_cutoffs[key] = max(
                deadline, self._stream_cutoffs.get(key, deadline)
            )
            return await super().remove_where(
                lambda task: task.analysis_id == analysis_id
                and task.camera_id == camera_id
                and task.raw_window is not None
                and task.raw_window.start_seconds < deadline
            )


class VLMWorker:
    """Background worker; one worker represents one inference slot."""

    def __init__(
        self,
        queue: TaskQueue[VLMTask],
        pipeline: SecurityAIPipeline,
        alert_store: AlertStore,
        event_bus: EventBus,
        analysis_store: AnalysisStore | None = None,
    ) -> None:
        self._queue = queue
        self._pipeline = pipeline
        self._alert_store = alert_store
        self._analysis_store = analysis_store
        self._event_bus = event_bus
        self._task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        logger.info("[vlm-worker] started")
        while True:
            delivery: Delivery[VLMTask] | None = None
            try:
                delivery = await self._queue.receive()
                task = delivery.task
                logger.info(
                    "[vlm-worker] processing alert=%s camera=%s rule=%s",
                    task.alert_id,
                    task.camera_id,
                    task.rule_id,
                )
                analysis = await self._process_task(task)
                await delivery.ack()
                logger.info(
                    "[vlm-worker] completed alert=%s level=%s",
                    task.alert_id,
                    analysis.alert_level.value,
                )
            except asyncio.CancelledError:
                if delivery is not None and delivery.state.value == "pending":
                    await delivery.retry()
                logger.info("[vlm-worker] cancelled")
                break
            except RejectMessageError:
                logger.exception("[vlm-worker] rejected invalid task")
                if delivery is not None:
                    await delivery.reject()
            except Exception:
                logger.exception("[vlm-worker] error processing task")
                if delivery is not None:
                    await delivery.retry()
        logger.info("[vlm-worker] stopped")

    async def _process_task(self, task: VLMTask) -> SceneAnalysis:
        """Process one item so video lifecycle behavior is independently testable."""
        processing_started = time.monotonic()
        if task.raw_window is not None:
            process_kwargs = {"stream_id": task.analysis_id or "default"}
            if task.admission is not None:
                process_kwargs["admission"] = task.admission
            try:
                processed = await asyncio.to_thread(
                    self._pipeline.process_video_window,
                    task.raw_window,
                    task.camera_id,
                    **process_kwargs,
                )
            except Exception:
                if task.admission is not None:
                    self._pipeline.fail_video_window_admission(task.admission)
                raise
            analysis = processed.scene
            qwen_ms = processed.timing.qwen_ms
            completed_at = time.monotonic()
            processed.timing.queue_wait_ms = max(
                0.0, (processing_started - task.enqueued_at) * 1000
            )
            processed.timing.wall_clock_ms = max(
                0.0, (completed_at - task.enqueued_at) * 1000
            )
        else:
            processed = None
            qwen_started = time.perf_counter()
            analysis = await asyncio.to_thread(self._pipeline.analyze_vlm, task)
            qwen_ms = (time.perf_counter() - qwen_started) * 1000

        await self._alert_store.update_vlm(
            task.alert_id, analysis, qwen_ms=qwen_ms
        )
        if task.analysis_id and self._analysis_store is not None:
            if processed is None:
                await self._analysis_store.complete_window(
                    task.analysis_id,
                    task.alert_id,
                    analysis,
                    qwen_ms=qwen_ms,
                )
            else:
                await self._analysis_store.complete_processed_window(
                    task.analysis_id,
                    task.alert_id,
                    processed,
                    qwen_ms,
                )
        if processed is not None:
            await self._alert_store.apply_episode_context(processed.alert_context)
            context = processed.alert_context
            deadline = context.next_recheck_event_seconds
            if (
                task.analysis_id
                and self._analysis_store is not None
                and deadline is not None
                and (context.episode_created or context.episode_extended)
            ):
                removed = await self._queue.suppress_stream_before(
                    analysis_id=task.analysis_id,
                    camera_id=task.camera_id,
                    deadline=deadline,
                )
                if removed:
                    removed_ids = await self._analysis_store.remove_pending_windows(
                        task.analysis_id,
                        {queued.alert_id for queued in removed},
                        context=context,
                        timebase="video",
                    )
                    for alert_id in removed_ids:
                        await self._alert_store.delete(alert_id)
        return analysis

    async def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._task = asyncio.ensure_future(self.run(), loop=loop)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
