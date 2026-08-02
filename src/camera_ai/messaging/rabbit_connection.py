"""Lazy robust RabbitMQ connection shared by optional adapters."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .config import RabbitMQSettings
from .contracts import BackendUnavailableError

Connector = Callable[[str], Awaitable[Any]]


class RabbitMQConnection:
    def __init__(self, settings: RabbitMQSettings, *, connector: Connector | None = None) -> None:
        self.settings = settings
        self._connector = connector or _default_connector
        self._connection: Any | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def channel(self, *, prefetch_count: int | None = None) -> Any:
        connection = await self._ensure_connection()
        channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
        if prefetch_count is not None:
            await channel.set_qos(prefetch_count=prefetch_count)
        return channel

    async def health(self) -> bool:
        return self._connection is not None and not bool(
            getattr(self._connection, "is_closed", False)
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def _ensure_connection(self) -> Any:
        if self._closed:
            raise RuntimeError("RabbitMQ connection is closed")
        if await self.health():
            return self._connection
        async with self._lock:
            if await self.health():
                return self._connection
            try:
                self._connection = await self._connector(self.settings.url)
            except ModuleNotFoundError as exc:
                if exc.name == "aio_pika" or str(exc) == "aio_pika":
                    raise BackendUnavailableError(
                        "RabbitMQ backend requires: pip install -e .[rabbitmq]"
                    ) from exc
                raise
            return self._connection


async def _default_connector(url: str) -> Any:
    try:
        import aio_pika
    except ModuleNotFoundError as exc:
        raise BackendUnavailableError(
            "RabbitMQ backend requires: pip install -e .[rabbitmq]"
        ) from exc
    return await aio_pika.connect_robust(url)
