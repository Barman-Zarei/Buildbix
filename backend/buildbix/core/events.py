"""Tiny event bus. Every published event is persisted to the audit log, then handed to subscribers."""
import logging
from collections import defaultdict
from typing import Callable

from sqlalchemy.orm import Session

log = logging.getLogger("buildbix.events")
_handlers: dict[str, list[Callable[[str, dict], None]]] = defaultdict(list)

PROJECT_CREATED = "project.created"
PROJECT_UPDATED = "project.updated"
BUILD_QUEUED = "build.queued"
BUILD_STARTED = "build.started"
BUILD_COMPLETED = "build.completed"
BUILD_FAILED = "build.failed"
BUILD_CANCELLED = "build.cancelled"
AUTOMATION_TRIGGERED = "automation.triggered"


def subscribe(event: str, handler: Callable[[str, dict], None]) -> None:
    """Subscribe to an event name, or '*' for all events."""
    _handlers[event].append(handler)


def publish(db: Session, event: str, *, workspace_id: int | None = None, actor_id: int | None = None,
            payload: dict | None = None) -> None:
    from ..models import AuditLog

    payload = payload or {}
    db.add(AuditLog(workspace_id=workspace_id, actor_id=actor_id, event=event, payload=payload))
    db.commit()
    for h in _handlers[event] + _handlers["*"]:
        try:
            h(event, payload)
        except Exception:  # subscribers must never break the publisher
            log.exception("event handler failed for %s", event)
