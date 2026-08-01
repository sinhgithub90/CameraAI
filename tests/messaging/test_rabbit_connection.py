from unittest.mock import AsyncMock

import pytest

from camera_ai.messaging.config import RabbitMQSettings
from camera_ai.messaging.contracts import BackendUnavailableError
from camera_ai.messaging.rabbit_connection import RabbitMQConnection


class FakeChannel:
    def __init__(self):
        self.set_qos = AsyncMock()
        self.close = AsyncMock()


class FakeConnection:
    def __init__(self):
        self.channel = AsyncMock(side_effect=lambda **_kwargs: FakeChannel())
        self.close = AsyncMock()
        self.is_closed = False


@pytest.mark.asyncio
async def test_connection_is_lazy_shared_and_closes_once():
    connection = FakeConnection()
    connector = AsyncMock(return_value=connection)
    client = RabbitMQConnection(RabbitMQSettings(), connector=connector)
    assert await client.health() is False

    first, second = await client.channel(prefetch_count=1), await client.channel()
    connector.assert_awaited_once()
    assert first.set_qos.await_args.kwargs == {"prefetch_count": 1}
    assert second is not first
    assert await client.health() is True

    await client.close()
    await client.close()
    connection.close.assert_awaited_once()
    assert await client.health() is False


@pytest.mark.asyncio
async def test_missing_optional_client_has_clear_error(monkeypatch):
    async def missing_connector(_url: str):
        raise ModuleNotFoundError("aio_pika")

    client = RabbitMQConnection(RabbitMQSettings(), connector=missing_connector)
    with pytest.raises(BackendUnavailableError, match=r"pip install -e .\[rabbitmq\]"):
        await client.channel()
