"""Task endpoints."""

from __future__ import annotations

from agentforge_shared.enums import TaskStatus
from agentforge_shared.models import Agent, Project, Task
from agentforge_shared.schemas import TaskCreate, TaskRead
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select

from ..deps import DbSession
from ..security import Principal, ensure_project_access, require_write
from ..services import create_task

router = APIRouter(tags=["tasks"])


def _get_task(session, task_id: str) -> Task:
    task = session.get(Task, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "task not found")
    return task


@router.get("/tasks", response_model=list[TaskRead])
def list_tasks(session: DbSession, project_id: str | None = None, limit: int = 200) -> list[Task]:
    stmt = select(Task).order_by(Task.created_at.desc()).limit(limit)
    if project_id:
        stmt = stmt.where(Task.project_id == project_id)
    return list(session.scalars(stmt))


@router.get("/tasks/{task_id}", response_model=TaskRead)
def get_task(session: DbSession, task_id: str) -> Task:
    return _get_task(session, task_id)


@router.post(
    "/projects/{project_id}/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    tags=["projects"],
)
def create_project_task(
    session: DbSession,
    project_id: str,
    payload: TaskCreate,
    principal: Principal = Depends(require_write),
) -> Task:
    """Queue a unit of work for a project (optionally pinned to one agent)."""
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    ensure_project_access(principal, project.id)
    if payload.agent_id is not None:
        agent = session.get(Agent, payload.agent_id)
        if agent is None or agent.project_id != project.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "agent does not belong to project")
    return create_task(
        session,
        project,
        payload.resolved_description(),
        payload.agent_id,
        kind=payload.kind,
        priority=payload.priority,
    )


@router.post(
    "/agents/{agent_id}/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
    tags=["agents"],
)
def create_agent_task(session: DbSession, agent_id: str, payload: TaskCreate) -> Task:
    """Queue work directly against one agent."""
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
    project = session.get(Project, agent.project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return create_task(
        session,
        project,
        payload.resolved_description(),
        agent.id,
        kind=payload.kind,
        priority=payload.priority,
    )


@router.post("/tasks/{task_id}/cancel", response_model=TaskRead)
def cancel_task(session: DbSession, task_id: str) -> Task:
    from agentforge_shared.enums import TaskStatus

    task = _get_task(session, task_id)
    if task.status in (TaskStatus.SUCCEEDED, TaskStatus.CANCELLED):
        return task
    task.status = TaskStatus.CANCELLED
    session.commit()
    session.refresh(task)
    return task


@router.get("/tasks/{task_id}/events")
def task_events(session: DbSession, task_id: str) -> dict:
    from agentforge_shared.models import Event

    task = _get_task(session, task_id)
    events = session.scalars(
        select(Event).where(Event.task_id == task.id).order_by(Event.created_at)
    )
    return {
        "task_id": task.id,
        "events": [
            {"id": e.id, "type": e.type, "payload": e.payload, "created_at": e.created_at}
            for e in events
        ],
    }


#: States a task can be dismissed from. Anything still in flight must be
#: cancelled first: deleting work someone is doing hides it rather than stopping
#: it.
TERMINAL_STATUSES = (
    TaskStatus.SUCCEEDED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.BLOCKED,
)


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(session: DbSession, task_id: str) -> None:
    task = _get_task(session, task_id)
    if task.status not in TERMINAL_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"task is {task.status}; cancel it before dismissing it",
        )
    session.delete(task)
    session.commit()


@router.delete("/projects/{project_id}/tasks")
def clear_finished_tasks(session: DbSession, project_id: str) -> dict:
    """Drop the finished tasks. The event log keeps the history."""
    result = session.execute(
        delete(Task).where(Task.project_id == project_id, Task.status.in_(TERMINAL_STATUSES))
    )
    session.commit()
    return {"deleted": result.rowcount or 0}
