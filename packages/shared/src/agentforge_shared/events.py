"""The single place events are written and matched.

Both the API and the orchestrator emit through here, so the catalog stays honest
and webhook matching has exactly one implementation.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .enums import EVENT_WILDCARD, EventType
from .models import Event


def record_event(
    session: Session,
    *,
    type: EventType | str,
    project_id: str | None = None,
    agent_id: str | None = None,
    task_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> Event:
    """Append an event to the project timeline.

    Never commits — the caller owns the transaction, so an event and the state
    change it describes land together or not at all.
    """
    event = Event(
        type=str(type),
        project_id=project_id,
        agent_id=agent_id,
        task_id=task_id,
        payload=payload or {},
    )
    session.add(event)
    return event


def serialise(event: Event) -> dict[str, Any]:
    """The wire representation used by both WebSockets and webhooks."""
    return {
        "id": event.id,
        "type": event.type,
        "project_id": event.project_id,
        "agent_id": event.agent_id,
        "task_id": event.task_id,
        "payload": event.payload or {},
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def matches(event_type: str, patterns: list[str] | None) -> bool:
    """Does an event type match a subscription's patterns?

    Supports exact matches and a trailing `.*` namespace wildcard, so
    `["agent.*"]` subscribes to the whole agent lifecycle.
    """
    if not patterns:
        return False
    for pattern in patterns:
        if pattern == EVENT_WILDCARD or pattern == event_type:
            return True
        if pattern.endswith(".*") and event_type.startswith(pattern[:-1]):
            return True
    return False
