import pytest

from camera_ai.messaging.rabbit_event_bus import _queue_id
from camera_ai.messaging.rabbit_task_queue import map_priority


def test_rabbit_task_priority_mapping():
    assert map_priority(1, max_priority=5) == 5
    assert map_priority(3, max_priority=5) == 3
    assert map_priority(5, max_priority=5) == 1
    with pytest.raises(ValueError):
        map_priority(0, max_priority=5)


def test_rabbit_queue_id_is_safe_and_non_empty():
    assert _queue_id("Rule Engine/1") == "rule-engine-1"
    with pytest.raises(ValueError):
        _queue_id("///")
