"""Task execution.

Design notes:

* A task runs on a worker thread, because every provider call is blocking
  (subprocess for DevPod, HTTP to the API server for exec). Doing it in the
  request path would hold an HTTP connection for minutes.
* Every state change is an event first and a row second, so an external client
  that is subscribed sees the transition even if it never polls (SPEC §33).
* The task lifecycle is the one in SPEC §19, and it is enforced here rather than
  left to the agent: `queued -> starting -> running -> completed|failed|cancelled`.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from .agents import AgentSpec, TaskRequest, get_agent_provider
from .db import session_scope
from .events import publish_sync
from .models import Task
from .workspaces import get_workspace_provider

log = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None
_cancelled: set[str] = set()
_lock = threading.Lock()


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="fleet-task")
    return _executor


def submit(task_id: str) -> None:
    _pool().submit(_run, task_id)


def cancel(task_id: str) -> None:
    with _lock:
        _cancelled.add(task_id)


def _is_cancelled(task_id: str) -> bool:
    with _lock:
        return task_id in _cancelled


def _clear(task_id: str) -> None:
    with _lock:
        _cancelled.discard(task_id)


def _now() -> datetime:
    return datetime.now(UTC)


def _emit(type: str, message: str, session_task: Task, **extra) -> None:
    publish_sync(
        type,
        message=message,
        project_id=session_task.project_id,
        agent_id=session_task.agent_id,
        task_id=session_task.id,
        payload=extra,
    )


def _set_status(task_id: str, status: str, **fields) -> Task | None:
    with session_scope() as session:
        task = session.get(Task, task_id)
        if task is None:
            return None
        task.status = status
        if status == "running" and task.started_at is None:
            task.started_at = _now()
        if status in {"completed", "failed", "cancelled"}:
            task.completed_at = _now()
        for key, value in fields.items():
            setattr(task, key, value)
        session.flush()
        session.expunge(task)
        return task


def _run(task_id: str) -> None:
    """Execute one task end to end."""
    with session_scope() as session:
        task = session.get(Task, task_id)
        if task is None:
            return
        session.expunge(task)

    from .models import Agent, Project  # local import keeps the module graph small

    with session_scope() as session:
        agent = session.get(Agent, task.agent_id) if task.agent_id else None
        project = session.get(Project, task.project_id)
        if agent is None or project is None:
            _set_status(task_id, "failed", error="task has no agent or project; it was deleted")
            return
        session.expunge(agent)
        session.expunge(project)

    workspaces = get_workspace_provider(project.workspace_provider or None)
    provider = get_agent_provider(agent.provider, workspaces)

    spec = AgentSpec(
        agent_id=agent.id,
        name=agent.name,
        project_id=project.id,
        project_name=project.name,
        workspace_reference=agent.config.get("workspace_reference", ""),
        provider=agent.provider,
        role=agent.role,
        model=agent.model or "",
        worktree=agent.worktree,
        config=agent.config or {},
    )

    if _is_cancelled(task_id):
        _set_status(task_id, "cancelled", error="cancelled before it started")
        _emit("task.cancelled", "task cancelled before it started", task)
        _clear(task_id)
        return

    _set_status(task_id, "starting")
    _emit("task.started", f"task handed to agent {spec.name}", task)

    request = TaskRequest(
        task_id=task.id,
        prompt=task.prompt,
        worktree=agent.worktree,
        branch=f"fleet/{agent.name}-{task.id}",
    )

    try:
        outcome = provider.execute_task(spec, request)
    except Exception as exc:  # noqa: BLE001 - a provider failure is task state
        log.exception("task %s failed in provider %s", task_id, provider.name)
        _set_status(task_id, "failed", error=f"{type(exc).__name__}: {exc}")
        _emit("task.failed", f"{type(exc).__name__}: {exc}", task, provider=provider.name)
        _clear(task_id)
        _release_agent(agent.id)
        return

    if _is_cancelled(task_id):
        _set_status(
            task_id,
            "cancelled",
            result=outcome.output[-4000:],
            error="cancelled while running",
        )
        _emit("task.cancelled", "task cancelled while running", task)
        _clear(task_id)
        _release_agent(agent.id)
        return

    fields = {
        "result": outcome.output[-8000:],
        "error": outcome.error[-4000:],
        "git_branch": outcome.git_branch,
        "git_commit": outcome.git_commit,
        "worktree": agent.worktree,
    }
    final = "completed" if outcome.status == "completed" else "failed"
    _set_status(task_id, final, **fields)
    _emit(
        "task.completed" if final == "completed" else "task.failed",
        outcome.output[-400:] if final == "completed" else outcome.error[-400:],
        task,
        git_branch=outcome.git_branch,
        git_commit=outcome.git_commit,
        files_changed=outcome.files_changed,
    )
    if outcome.git_commit:
        _emit(
            "commit.created",
            f"commit {outcome.git_commit[:12]} on {outcome.git_branch}",
            task,
            commit=outcome.git_commit,
            branch=outcome.git_branch,
        )
    _clear(task_id)
    _release_agent(agent.id)


def _release_agent(agent_id: str | None) -> None:
    if not agent_id:
        return
    from .models import Agent

    with session_scope() as session:
        agent = session.get(Agent, agent_id)
        if agent is not None:
            agent.status = "idle"
            agent.current_task_id = None
