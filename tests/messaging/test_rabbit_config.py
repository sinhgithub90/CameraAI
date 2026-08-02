import pytest

from camera_ai.messaging.config import MessagingSettings, RabbitMQSettings


def test_rabbit_settings_defaults_and_redacted_repr(monkeypatch):
    monkeypatch.setenv("RABBITMQ_URL", "amqp://user:secret@broker/vhost")
    settings = RabbitMQSettings.from_env()
    assert settings.event_exchange == "camera_ai.events"
    assert settings.task_exchange == "camera_ai.tasks"
    assert settings.prefetch_vlm == 1
    assert "secret" not in repr(settings)


def test_invalid_backend_is_rejected(monkeypatch):
    monkeypatch.setenv("CAMERA_AI_EVENT_BUS_BACKEND", "kafka")
    with pytest.raises(ValueError, match="inprocess|rabbitmq"):
        MessagingSettings.from_env()
