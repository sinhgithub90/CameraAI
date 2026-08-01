"""Backward-compatible public event bus imports."""

from .messaging.event_bus import Event, EventBus, EventHandler
from .messaging.inprocess import InProcessEventBus

__all__ = ["Event", "EventBus", "EventHandler", "InProcessEventBus"]
