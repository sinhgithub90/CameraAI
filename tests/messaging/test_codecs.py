from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pytest

from camera_ai.messaging.codecs import JSONMessageCodec
from camera_ai.messaging.contracts import SerializationError, UnsupportedSchemaError


@dataclass(frozen=True)
class DemoTask:
    task_id: str
    value: int


def _codec() -> JSONMessageCodec[DemoTask]:
    return JSONMessageCodec(
        message_type="demo.task",
        to_payload=lambda task: {"task_id": task.task_id, "value": task.value},
        from_payload=lambda payload: DemoTask(
            task_id=str(payload["task_id"]), value=int(payload["value"])
        ),
    )


def test_json_codec_round_trip_preserves_envelope_metadata():
    codec = _codec()
    occurred_at = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    encoded = codec.encode(
        DemoTask("task-1", 7),
        message_id="message-1",
        source="rule-engine",
        camera_id="cam-01",
        occurred_at=occurred_at,
        correlation_id="analysis-1",
        causation_id="candidate-1",
    )
    decoded = codec.decode(encoded)
    assert decoded.value == DemoTask("task-1", 7)
    assert decoded.envelope.message_id == "message-1"
    assert decoded.envelope.occurred_at == occurred_at
    assert decoded.envelope.schema_version == 1


@pytest.mark.parametrize("unsafe", [b"jpeg", np.zeros((2, 2, 3)), float("nan")])
def test_json_codec_rejects_non_transport_values(unsafe):
    codec = JSONMessageCodec(
        message_type="unsafe",
        to_payload=lambda _value: {"unsafe": unsafe},
        from_payload=lambda payload: payload,
    )
    with pytest.raises(SerializationError):
        codec.encode(object(), source="test", camera_id="cam-01")


def test_json_codec_rejects_unknown_schema_version():
    body = _codec().encode(DemoTask("task-1", 1), source="test", camera_id="cam")
    mutated = body.replace(b'"schema_version":1', b'"schema_version":99')
    with pytest.raises(UnsupportedSchemaError):
        _codec().decode(mutated)
