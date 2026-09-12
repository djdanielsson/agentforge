"""In-process publish/subscribe hub backing the WebSocket event streams.

Single-process for MVP. When we scale the API out, this becomes a Postgres
LISTEN/NOTIFY or Redis stream — the interface stays the same.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict
from typing import Any


class EventHub:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, topic: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._subscribers[topic].add(queue)
        return queue

    async def unsubscribe(self, topic: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers[topic].discard(queue)
            if not self._subscribers[topic]:
                self._subscribers.pop(topic, None)

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(topic, ()))
            # Agents are also interested in their project's topic.
            queues += list(self._subscribers.get("*", ()))
        for queue in queues:
            with contextlib.suppress(asyncio.QueueFull):  # drop, never block agents
                queue.put_nowait(message)


hub = EventHub()
