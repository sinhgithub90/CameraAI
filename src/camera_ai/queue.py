# src/camera_ai/queue.py
"""VLM priority queue and background worker for async processing."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from .schemas import Detection, SceneAnalysis

if TYPE_CHECKING:
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
    frames: list[np.ndarray] = field(default_factory=list, compare=False)
    detections: list[Detection] = field(default_factory=list, compare=False)
    rule_id: str = field(default="default", compare=False)
    max_keyframes: int = field(default=2, compare=False)

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

    Uses asyncio.PriorityQueue under the hood. Lower priority number = higher
    urgency. Dynamic priority aging prevents starvation of low-priority tasks.
    """

    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue[VLMTask] = asyncio.PriorityQueue()

    async def enqueue(self, task: VLMTask) -> None:
        await self._queue.put(task)
        logger.debug(
            "[vlm-queue] enqueued alert=%s priority=%s depth=%s",
            task.alert_id,
            task.priority,
            self._queue.qsize(),
        )

    async def dequeue(self) -> VLMTask:
        task = await self._queue.get()
        now = time.monotonic()
        eff = task.effective_priority(now=now)
        if eff < task.priority:
            # Task aged — re-push with boosted priority, then re-pop.
            aged = VLMTask(
                priority=eff,
                enqueued_at=task.enqueued_at,   # keep original timestamp
                task_id=task.task_id,
                camera_id=task.camera_id,
                alert_id=task.alert_id,
                frames=task.frames,
                detections=task.detections,
                rule_id=task.rule_id,
                max_keyframes=task.max_keyframes,
            )
            logger.debug(
                "[vlm-queue] aged alert=%s priority=%s→%s wait=%.0fs",
                task.alert_id,
                task.priority,
                eff,
                now - task.enqueued_at,
            )
            await self._queue.put(aged)
            return await self.dequeue()
        logger.debug(
            "[vlm-queue] dequeued alert=%s priority=%s depth=%s",
            task.alert_id,
            task.priority,
            self._queue.qsize(),
        )
        return task

    @property
    def depth(self) -> int:
        return self._queue.qsize()


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
    ) -> None:
        self._queue = queue
        self._pipeline = pipeline
        self._alert_store = alert_store
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
                # analyze_vlm is sync (blocking Ollama I/O) — offload to thread
                analysis = await asyncio.to_thread(
                    self._pipeline.analyze_vlm, task
                )
                await self._alert_store.update_vlm(task.alert_id, analysis)
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
