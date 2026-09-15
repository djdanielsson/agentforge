"""Events: a list endpoint and a Server-Sent Events stream (SPEC §21, §33).

SSE rather than WebSocket because the traffic is one-directional and a stream
survives reconnection via a cursor (`?since=<event id>`), which a WebSocket
would need us to reimplement.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from fleet_core.db import session_scope
from fleet_core.events import bus
from fleet_core.models import Event

from ..security import require_token

router = APIRouter(prefix="/api/v1", tags=["events"])

#: SSE endpoints are consumed by EventSource, which cannot set headers, so the
#: browser UI uses the query-string token. The list endpoint requires the header.
PUBLIC_STREAM = True


@router.get("/events")
def list_events(
    project: str = Query(default=""),
    task: str = Query(default=""),
    limit: int = Query(default=100, le=1000),
    since: int = Query(default=0),
    _: str = Depends(require_token),
) -> dict[str, Any]:
    with session_scope() as session:
        query = select(Event).order_by(Event.id.desc()).limit(limit)
        if since:
            query = select(Event).where(Event.id > since).order_by(Event.id.asc()).limit(limit)
        if project:
            query = query.where(Event.project_id == project)
        if task:
            query = query.where(Event.task_id == task)
        rows = session.execute(query).scalars().all()
        items = [
            {
                "id": r.id,
                "type": r.type,
                "message": r.message,
                "project_id": r.project_id,
                "agent_id": r.agent_id,
                "task_id": r.task_id,
                "payload": r.payload,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    if not since:
        items.reverse()
    return {"items": items, "count": len(items)}


@router.get("/events/stream")
async def stream_events(request: Request, since: int = Query(default=0)) -> StreamingResponse:
    async def generator():
        # Replay first so a client that reconnects with a cursor does not miss
        # what happened while it was away.
        if since:
            with session_scope() as session:
                rows = session.execute(
                    select(Event).where(Event.id > since).order_by(Event.id.asc()).limit(200)
                ).scalars().all()
                for row in rows:
                    yield _sse(
                        {
                            "id": row.id,
                            "type": row.type,
                            "message": row.message,
                            "project_id": row.project_id,
                            "agent_id": row.agent_id,
                            "task_id": row.task_id,
                            "payload": row.payload,
                            "created_at": row.created_at.isoformat(),
                        }
                    )
        yield _sse({"type": "stream.ready"})
        subscription = bus.subscribe()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    record = await asyncio.wait_for(subscription.__anext__(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                except StopAsyncIteration:
                    break
                yield _sse(record)
        finally:
            await subscription.aclose()

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"
