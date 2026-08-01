"""Strict JSON codecs used by all transport implementations."""
from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Generic, TypeVar

from .contracts import (
    DecodedMessage,
    JSONValue,
    MessageEnvelope,
    SerializationError,
    UnsupportedSchemaError,
)

T = TypeVar("T")
SCHEMA_VERSION = 1


class JSONMessageCodec(Generic[T]):
    """Encode one typed payload in a versioned, JSON-only envelope."""

    def __init__(
        self,
        *,
        message_type: str,
        to_payload: Callable[[T], Mapping[str, JSONValue]],
        from_payload: Callable[[Mapping[str, JSONValue]], T],
    ) -> None:
        self.message_type = message_type
        self._to_payload = to_payload
        self._from_payload = from_payload

    def encode(self, value: T, **metadata: object) -> bytes:
        occurred_at = metadata.get("occurred_at")
        if occurred_at is None:
            occurred_at = datetime.now(timezone.utc)
        if not isinstance(occurred_at, datetime):
            raise SerializationError("occurred_at must be a datetime")
        if occurred_at.tzinfo is None:
            raise SerializationError("occurred_at must include a timezone")

        try:
            payload = dict(self._to_payload(value))
            document = {
                "message_id": str(metadata.get("message_id") or uuid.uuid4().hex),
                "message_type": self.message_type,
                "source": str(metadata.get("source") or "unknown"),
                "camera_id": str(metadata.get("camera_id") or "unknown"),
                "occurred_at": occurred_at.astimezone(timezone.utc).isoformat(),
                "correlation_id": _optional_string(metadata.get("correlation_id")),
                "causation_id": _optional_string(metadata.get("causation_id")),
                "schema_version": SCHEMA_VERSION,
                "payload": payload,
            }
            return json.dumps(
                document,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise SerializationError(f"message is not JSON-serializable: {exc}") from exc

    def decode(self, body: bytes) -> DecodedMessage[T]:
        try:
            document = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SerializationError("message body is not valid UTF-8 JSON") from exc
        if not isinstance(document, dict):
            raise SerializationError("message envelope must be a JSON object")
        if document.get("schema_version") != SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                f"unsupported schema version: {document.get('schema_version')!r}"
            )
        payload = document.get("payload")
        if not isinstance(payload, dict):
            raise SerializationError("message payload must be a JSON object")
        try:
            occurred_at = datetime.fromisoformat(str(document["occurred_at"]))
            if occurred_at.tzinfo is None:
                raise ValueError("occurred_at must include a timezone")
            envelope = MessageEnvelope(
                message_id=_required_string(document, "message_id"),
                message_type=_required_string(document, "message_type"),
                source=_required_string(document, "source"),
                camera_id=_required_string(document, "camera_id"),
                occurred_at=occurred_at,
                correlation_id=_optional_string(document.get("correlation_id")),
                causation_id=_optional_string(document.get("causation_id")),
                schema_version=SCHEMA_VERSION,
                payload=payload,
            )
            value = self._from_payload(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise SerializationError(f"invalid message envelope: {exc}") from exc
        return DecodedMessage(value=value, envelope=envelope)


def _required_string(document: Mapping[str, object], field: str) -> str:
    value = document[field]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional IDs must be strings")
    return value
