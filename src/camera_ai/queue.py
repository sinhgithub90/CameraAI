# src/camera_ai/queue.py
"""VLM priority queue and background worker for async processing."""
from __future__ import annotations

import asyncio
import heapq
import logging
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Callable

import numpy as np

from .alert_cooldown import WindowAdmission
from .schemas import Detection, SceneAnalysis
from .video_windows import RawVideoWindow

if TYPE_CHECKING:
    from .analysis_store import AnalysisStore
    from .alert_store import AlertStore
    from .events import EventBus
    from .pipeline import SecurityAIPipeline

logger = logging.getLogger(__name__)

# Priority aging thresholds
AGING_30S = 30.0
AGING_60S = 60.0
AGING_120S = 120.0


@dataclass(order=True)
class VLMTask:
    """One VLM analysis task in the priority queue."""

    # Sort fields (order=True uses these in declaration order)
    priority: int
    enqueued_at: float

    # Non-sort fields
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


class VLMQueue:
    """Priority queue for VLM analysis tasks.

    One condition protects the heap across enqueue, dequeue, and pruning.
    Lower priority number means higher urgency; aging prevents starvation.
    """

    def __init__(self) -> None:
        self._heap: list[VLMTask] = []
        self._condition = asyncio.Condition()
        self._stream_cutoffs: dict[tuple[str, str], float] = {}

    async def enqueue(self, task: VLMTask) -> bool:
        async with self._condition:
            cutoff = self._stream_cutoffs.get((task.analysis_id, task.camera_id))
            if (
                cutoff is not None
                and task.raw_window is not None
                and task.raw_window.start_seconds < cutoff
            ):
                return False
            heapq.heappush(self._heap, task)
            self._condition.notify()
        logger.debug(
            "[vlm-queue] enqueued alert=%s priority=%s depth=%s",
            task.alert_id,
            task.priority,
            self.depth,
        )
        return True

    async def dequeue(self) -> VLMTask:
        async with self._condition:
            while True:
                while not self._heap:
                    await self._condition.wait()
                task = heapq.heappop(self._heap)
                effective = task.effective_priority(now=time.monotonic())
                if effective < task.priority:
                    heapq.heappush(
                        self._heap, replace(task, priority=effective)
                    )
                    continue
                break
        logger.debug(
            "[vlm-queue] dequeued alert=%s priority=%s depth=%s",
            task.alert_id,
            task.priority,
            self.depth,
        )
        return task

    @property
    def depth(self) -> int:
        return len(self._heap)

    async def remove_where(
        self, predicate: Callable[[VLMTask], bool]
    ) -> list[VLMTask]:
        """Remove matching pending tasks while holding the queue lock."""
        async with self._condition:
            removed = [task for task in self._heap if predicate(task)]
            if removed:
                self._heap = [task for task in self._heap if not predicate(task)]
                heapq.heapify(self._heap)
            return removed

    async def suppress_stream_before(
        self, *, analysis_id: str, camera_id: str, deadline: float
    ) -> list[VLMTask]:
        """Atomically register a cutoff and remove already queued stale work."""
        async with self._condition:
            key = (analysis_id, camera_id)
            self._stream_cutoffs[key] = max(
                deadline, self._stream_cutoffs.get(key, deadline)
            )
            removed = [
                task
                for task in self._heap
                if task.analysis_id == analysis_id
                and task.camera_id == camera_id
                and task.raw_window is not None
                and task.raw_window.start_seconds < deadline
            ]
            if removed:
                removed_ids = {task.task_id for task in removed}
                self._heap = [
                    task for task in self._heap if task.task_id not in removed_ids
                ]
                heapq.heapify(self._heap)
            return removed


class VLMWorker:
    """Background worker that dequeues VLM tasks and runs analysis.

    One worker = one asyncio task = one GPU inference slot. For multi-GPU,
    create multiple workers pointing to the same queue.
    """

    def __init__(
        self,
        queue: VLMQueue,
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
        """Infinite loop: dequeue → analyze → update. Run as asyncio task."""
        logger.info("[vlm-worker] started")
        while True:
            try:
                task = await self._queue.dequeue()
                logger.info(
                    "[vlm-worker] processing alert=%s camera=%s rule=%s",
                    task.alert_id,
                    task.camera_id,
                    task.rule_id,
                )
                analysis = await self._process_task(task)
                logger.info(
                    "[vlm-worker] completed alert=%s level=%s",
                    task.alert_id,
                    analysis.alert_level.value,
                )
            except asyncio.CancelledError:
                logger.info("[vlm-worker] cancelled")
                break
            except Exception:
                logger.exception(
                    "[vlm-worker] error processing alert=%s", task.alert_id
                )
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
        """Create and track the background task so it can be cancelled on stop."""
        self._task = asyncio.ensure_future(self.run(), loop=loop)

    async def stop(self) -> None:
        """Cancel the background task and wait for it to finish."""
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
