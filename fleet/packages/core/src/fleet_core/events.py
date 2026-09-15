"""The event bus (SPEC §21, §33).

Every subsystem publishes here; the DB row is the durable log and the in-process
queues feed the SSE stream. Nothing couples the UI to the task runner.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from .db import session_scope
from .models import Event

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()
        # Task execution runs on worker threads; the queues belong to the event
        # loop, so publishing from a thread has to hop back onto it.
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _persist(
        self,
        type: str,
        message: str,
        project_id: str | None,
        agent_id: str | None,
        task_id: str | None,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        with session_scope() as session:
            row = Event(
                project_id=project_id,
                agent_id=agent_id,
                task_id=task_id,
                type=type,
                message=message,
                payload=payload or {},
            )
            session.add(row)
            session.flush()
            return {
                "id": row.id,
                "type": row.type,
                "message": row.message,
                "project_id": row.project_id,
                "agent_id": row.agent_id,
                "task_id": row.task_id,
                "payload": row.payload,
                "created_at": row.created_at.isoformat(),
            }

    def _fanout(self, record: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(record)
            except asyncio.QueueFull:
                # A slow consumer must not stall the producer.
                log.warning("dropping event for a full subscriber queue")

    async def publish(
        self,
        type: str,
        *,
        message: str = "",
        project_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self._persist(type, message, project_id, agent_id, task_id, payload)
        async with self._lock:
            self._fanout(record)
        return record

    def publish_sync(
        self,
        type: str,
        *,
        message: str = "",
        project_id: str | None = None,
        agent_id: str | None = None,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Publish from a worker thread. Durable first, then delivered."""
        record = self._persist(type, message, project_id, agent_id, task_id, payload)
        loop = self._loop
        if loop is None or loop.is_closed():
            log.debug("no event loop bound; event %s stored but not streamed", record["id"])
            return record
        loop.call_soon_threadsafe(self._fanout, record)
        return record

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = EventBus()


async def publish(type: str, **kwargs: Any) -> dict[str, Any]:
    return await bus.publish(type, **kwargs)


def publish_sync(type: str, **kwargs: Any) -> dict[str, Any]:
    return bus.publish_sync(type, **kwargs)
