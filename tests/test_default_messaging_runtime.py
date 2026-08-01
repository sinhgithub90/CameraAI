from apps.api import main
from camera_ai.events import InProcessEventBus
from camera_ai.queue import VLMQueue


def test_default_api_runtime_remains_inprocess():
    assert isinstance(main.event_bus, InProcessEventBus)
    assert isinstance(main.vlm_queue, VLMQueue)
