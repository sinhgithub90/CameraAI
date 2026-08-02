import sys

import pytest

from camera_ai.messaging.config import MessagingSettings
from camera_ai.messaging.factory import build_event_bus, build_task_queue
from camera_ai.messaging.inprocess import InProcessEventBus, InProcessTaskQueue
from camera_ai.messaging.rabbit_event_bus import RabbitMQEventBus


def test_default_factory_builds_inprocess_without_importing_aio_pika(monkeypatch):
    settings = MessagingSettings(event_bus_backend="inprocess", vlm_queue_backend="inprocess")
    monkeypatch.setitem(sys.modules, "aio_pika", None)
    assert isinstance(build_event_bus(settings), InProcessEventBus)
    assert isinstance(build_task_queue("anything", settings), InProcessTaskQueue)


def test_rabbit_event_factory_is_lazy():
    settings = MessagingSettings(event_bus_backend="rabbitmq")
    assert isinstance(build_event_bus(settings), RabbitMQEventBus)


def test_rabbit_task_factory_requires_transport_safe_codec():
    settings = MessagingSettings(vlm_queue_backend="rabbitmq")
    with pytest.raises(ValueError, match="FrameStore and VLMJob"):
        build_task_queue("vlm", settings)
