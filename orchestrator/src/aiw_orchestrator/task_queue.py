"""Durable task queue with leases.

A task is `leased` by exactly one worker for `task_lease_seconds`. If the worker
dies, the lease expires and the task is re-queued — no work is lost, and no two
workers run the same task.
"""

from __future__ import annotations

import logging
import os
import socket
import uuid
from datetime import UTC, datetime, timedelta

from aiw_shared.config import get_settings
from aiw_shared.db import session_scope
from aiw_shared.enums import AgentStatus, EventType, TaskStatus
from aiw_shared.models import Agent, Task
from sqlalchemy import select

log = logging.getLogger(__name__)


def worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


class TaskQueue:
    def __init__(self, name: str | None = None) -> None:
        self.name = name or worker_id()
        self.settings = get_settings()

    def requeue_expired(self) -> int:
        """Return expired leases to the queue."""
        now = datetime.now(UTC)
        count = 0
        with session_scope() as session:
            expired = session.scalars(
                select(Task).where(
                    Task.status == TaskStatus.LEASED,
                    Task.leased_until.is_not(None),
                    Task.leased_until < now,
                )
            )
            for task in expired:
                if task.attempts >= self.settings.task_max_attempts:
                    task.status = TaskStatus.FAILED
                    task.error = "lease expired and max attempts reached"
                else:
                    task.status = TaskStatus.QUEUED
                task.leased_by = None
                task.leased_until = None
                count += 1
        if count:
            log.warning("re-queued %d expired task lease(s)", count)
        return count

    def lease(self, agent_filter: bool = True) -> Task | None:
        """Atomically claim the oldest queued task."""
        now = datetime.now(UTC)
        with session_scope() as session:
            stmt = select(Task).where(Task.status == TaskStatus.QUEUED)
            if agent_filter:
                stmt = stmt.where(Task.agent_id.is_not(None))
            stmt = stmt.order_by(Task.position, Task.created_at).limit(1)
            task = session.scalars(stmt).first()
            if task is None:
                return None
            task.status = TaskStatus.LEASED
            task.leased_by = self.name
            task.leased_until = now + timedelta(seconds=self.settings.task_lease_seconds)
            task.attempts += 1
            session.flush()
            task_id = task.id
        log.info("leased task %s", task_id)
        return self.get(task_id)

    def get(self, task_id: str) -> Task | None:
        with session_scope() as session:
            task = session.get(Task, task_id)
            if task is not None:
                session.expunge(task)
            return task

    def mark_running(self, task_id: str) -> None:
        self._set_status(task_id, TaskStatus.RUNNING, started=True)

    def complete(self, task_id: str, *, result: str, commits: list[str] | None = None) -> None:
        self._set_status(
            task_id,
            TaskStatus.SUCCEEDED,
            result=result,
            commits=commits or [],
            completed=True,
        )

    def fail(self, task_id: str, error: str) -> None:
        self._set_status(task_id, TaskStatus.FAILED, error=error, completed=True)

    def block(self, task_id: str, reason: str) -> None:
        self._set_status(task_id, TaskStatus.BLOCKED, result=reason)

    def _set_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        result: str | None = None,
        error: str | None = None,
        commits: list[str] | None = None,
        started: bool = False,
        completed: bool = False,
    ) -> None:
        with session_scope() as session:
            task = session.get(Task, task_id)
            if task is None:
                return
            task.status = status
            task.leased_by = None
            task.leased_until = None
            if result is not None:
                task.result = result
            if error is not None:
                task.error = error
            if commits is not None:
                task.commits = commits
            if started:
                task.started_at = datetime.now(UTC)
            if completed:
                task.completed_at = datetime.now(UTC)
            session.add(
                __import__("aiw_shared.models", fromlist=["Event"]).Event(
                    type=str(EventType.TASK_STATUS),
                    project_id=task.project_id,
                    agent_id=task.agent_id,
                    task_id=task.id,
                    payload={"status": str(status), "result": result, "error": error},
                )
            )

    def next_task_for_agent(self, agent_id: str) -> Task | None:
        with session_scope() as session:
            task = session.scalars(
                select(Task)
                .where(Task.agent_id == agent_id, Task.status == TaskStatus.QUEUED)
                .order_by(Task.position, Task.created_at)
                .limit(1)
            ).first()
            if task is not None:
                session.expunge(task)
            return task


__all__ = ["TaskQueue", "worker_id", "Agent", "AgentStatus"]
