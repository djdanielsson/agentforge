"""WebSocket event streams.

`ws://.../projects/{id}/events`  — everything that happens in a project
`ws://.../agents/{id}/events`    — one agent's timeline, backfilled on connect
"""

from __future__ import annotations

import asyncio
import contextlib

from aiw_shared.db import session_factory
from aiw_shared.models import Agent, Event, Project
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..events import hub

router = APIRouter(tags=["stream"])

BACKFILL_LIMIT = 200


def _serialise(event: Event) -> dict:
    return {
        "id": event.id,
        "type": event.type,
        "project_id": event.project_id,
        "agent_id": event.agent_id,
        "task_id": event.task_id,
        "payload": event.payload or {},
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


async def _stream(websocket: WebSocket, topic: str, backfill: list[dict]) -> None:
    await websocket.accept()
    queue = await hub.subscribe(topic)
    try:
        for message in backfill:
            await websocket.send_json(message)
        while True:
            message = await queue.get()
            await websocket.send_json(message)
    except WebSocketDisconnect:
        pass
    except Exception:  # pragma: no cover - transport level
        pass
    finally:
        await hub.unsubscribe(topic, queue)
        with contextlib.suppress(Exception):
            await websocket.close()


@router.websocket("/projects/{project_id}/events")
async def project_events(websocket: WebSocket, project_id: str) -> None:
    with session_factory() as session:
        if session.get(Project, project_id) is None:
            await websocket.close(code=4404)
            return
        events = session.scalars(
            select(Event)
            .where(Event.project_id == project_id)
            .order_by(Event.created_at.desc())
            .limit(BACKFILL_LIMIT)
        )
        backfill = [_serialise(e) for e in reversed(list(events))]
    await _stream(websocket, f"project:{project_id}", backfill)


@router.websocket("/agents/{agent_id}/events")
async def agent_events(websocket: WebSocket, agent_id: str) -> None:
    with session_factory() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            await websocket.close(code=4404)
            return
        events = session.scalars(
            select(Event)
            .where(Event.agent_id == agent_id)
            .order_by(Event.created_at.desc())
            .limit(BACKFILL_LIMIT)
        )
        backfill = [_serialise(e) for e in reversed(list(events))]
    await _stream(websocket, f"agent:{agent_id}", backfill)


__all__ = ["router", "asyncio"]
