"""Semantic actions and introspection.

A caller should be able to say *what* they want — review this, test this, deploy
this — without knowing that a task row, an agent turn and a git branch are
involved. That translation lives here.
"""

from __future__ import annotations

from agentforge_shared.enums import AgentStatus, EventType, TaskKind, TaskStatus
from agentforge_shared.models import Agent, Event, Project, Task
from agentforge_shared.schemas import (
    AgentSummary,
    DeployRequest,
    EventTypeInfo,
    ReviewRequest,
    TaskRead,
    TestRequest,
)
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from ..deps import DbSession
from ..security import Principal, ensure_project_access, require_write
from ..services import create_task

router = APIRouter(tags=["actions"])


EVENT_CATALOG: list[EventTypeInfo] = [
    EventTypeInfo(
        type="project.created",
        description="A project was registered.",
        payload_fields=["name", "repository_url"],
    ),
    EventTypeInfo(
        type="project.status", description="Project lifecycle changed.", payload_fields=["status"]
    ),
    EventTypeInfo(
        type="workspace.created",
        description="A workspace's k8s objects exist.",
        payload_fields=["namespace"],
    ),
    EventTypeInfo(
        type="workspace.ready",
        description="The workspace pod is Ready.",
        payload_fields=["code_server_url"],
    ),
    EventTypeInfo(
        type="workspace.destroyed",
        description="The workspace namespace was deleted.",
        payload_fields=["namespace"],
    ),
    EventTypeInfo(
        type="workspace.failed", description="Provisioning failed.", payload_fields=["error"]
    ),
    EventTypeInfo(
        type="agent.created",
        description="An agent was created.",
        payload_fields=["name", "model", "branch"],
    ),
    EventTypeInfo(
        type="agent.started",
        description="The agent runtime accepted the session.",
        payload_fields=["session_id"],
    ),
    EventTypeInfo(
        type="agent.progress",
        description="The agent made observable progress.",
        payload_fields=["message", "files_changed"],
    ),
    EventTypeInfo(
        type="agent.message",
        description="A conversational turn.",
        payload_fields=["role", "content"],
    ),
    EventTypeInfo(
        type="agent.waiting",
        description="The agent needs a human answer.",
        payload_fields=["question"],
    ),
    EventTypeInfo(
        type="agent.permission_required",
        description="The agent wants to run something.",
        payload_fields=["request_id", "command", "reason"],
    ),
    EventTypeInfo(
        type="agent.completed",
        description="The agent finished a task.",
        payload_fields=["task_id", "result"],
    ),
    EventTypeInfo(type="agent.failed", description="The agent errored.", payload_fields=["error"]),
    EventTypeInfo(type="agent.stopped", description="The agent was stopped.", payload_fields=[]),
    EventTypeInfo(
        type="permission.granted",
        description="A permission request was allowed.",
        payload_fields=["request_id", "decision"],
    ),
    EventTypeInfo(
        type="permission.denied",
        description="A permission request was denied.",
        payload_fields=["request_id"],
    ),
    EventTypeInfo(
        type="task.created",
        description="Work was queued.",
        payload_fields=["description", "kind", "priority"],
    ),
    EventTypeInfo(
        type="task.status",
        description="Task lifecycle changed.",
        payload_fields=["status", "result", "error"],
    ),
    EventTypeInfo(
        type="test.completed",
        description="A test run finished.",
        payload_fields=["passed", "failed", "command"],
    ),
    EventTypeInfo(
        type="review.completed",
        description="A review finished.",
        payload_fields=["branch", "files_changed", "summary"],
    ),
    EventTypeInfo(
        type="deploy.completed",
        description="A deployment finished.",
        payload_fields=["environment", "branch"],
    ),
    EventTypeInfo(
        type="commit.created",
        description="The agent committed code.",
        payload_fields=["commit", "branch"],
    ),
]


def _project(session, project_id: str, principal: Principal) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    ensure_project_access(principal, project.id)
    return project


def _queue(
    session,
    project: Project,
    *,
    kind: TaskKind,
    description: str,
    agent_id: str | None,
    payload: dict,
) -> Task:
    """Shared tail of every semantic action."""
    if agent_id is None and project.agents:
        agent_id = project.agents[0].id
    if agent_id is not None:
        agent = session.get(Agent, agent_id)
        if agent is None or agent.project_id != project.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "agent does not belong to project")
    task = create_task(session, project, description, agent_id, kind=kind, payload=payload)
    return task


@router.get("/providers")
def list_providers() -> dict:
    """Which workspace backends exist and what each can actually do.

    Advertised from the providers themselves, so the UI disables a control the
    selected backend cannot honour instead of offering it and failing later.
    """
    from agentforge_shared.config import get_settings
    from agentforge_workspaces.providers import available_providers, get_provider

    settings = get_settings()
    descriptions = []
    for name in available_providers():
        try:
            descriptions.append(get_provider(name).capabilities())
        except Exception as exc:  # noqa: BLE001 - a backend may be unavailable here
            descriptions.append({"provider": name, "available": False, "error": str(exc)[:200]})
    return {"configured": settings.workspace_provider, "providers": descriptions}


@router.get("/events/catalog", response_model=list[EventTypeInfo])
def event_catalog() -> list[EventTypeInfo]:
    """Discover what a webhook can subscribe to."""
    return EVENT_CATALOG


@router.post(
    "/projects/{project_id}/review", response_model=TaskRead, status_code=status.HTTP_202_ACCEPTED
)
def review(
    session: DbSession,
    project_id: str,
    payload: ReviewRequest,
    principal: Principal = Depends(require_write),
) -> Task:
    """Have an agent review a branch and report what it finds."""
    project = _project(session, project_id, principal)
    branch = payload.branch or project.default_branch
    return _queue(
        session,
        project,
        kind=TaskKind.REVIEW,
        description=f"Review branch {branch} against {payload.base or project.default_branch}",
        agent_id=payload.agent_id,
        payload={"branch": branch, "base": payload.base},
    )


@router.post(
    "/projects/{project_id}/test", response_model=TaskRead, status_code=status.HTTP_202_ACCEPTED
)
def test(
    session: DbSession,
    project_id: str,
    payload: TestRequest,
    principal: Principal = Depends(require_write),
) -> Task:
    """Run the project's test suite and report the result as `test.completed`."""
    project = _project(session, project_id, principal)
    branch = payload.branch or project.default_branch
    command = payload.command or (project.settings or {}).get("test_command", "make test")
    return _queue(
        session,
        project,
        kind=TaskKind.TEST,
        description=f"Run tests on {branch}: {command}",
        agent_id=payload.agent_id,
        payload={"branch": branch, "command": command},
    )


@router.post(
    "/projects/{project_id}/deploy", response_model=TaskRead, status_code=status.HTTP_202_ACCEPTED
)
def deploy(
    session: DbSession,
    project_id: str,
    payload: DeployRequest,
    principal: Principal = Depends(require_write),
) -> Task:
    """Deploy a branch to an environment. Emits `deploy.completed`."""
    project = _project(session, project_id, principal)
    branch = payload.branch or project.default_branch
    return _queue(
        session,
        project,
        kind=TaskKind.DEPLOY,
        description=f"Deploy {branch} to {payload.environment}",
        agent_id=payload.agent_id,
        payload={"branch": branch, "environment": payload.environment},
    )


@router.get("/agents/{agent_id}/summary", response_model=AgentSummary)
def agent_summary(
    session: DbSession, agent_id: str, principal: Principal = Depends(require_write)
) -> AgentSummary:
    """One call that answers: what is this agent doing, and is it stuck?

    This is the endpoint a conversational interface polls or is pushed to.
    """
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")

    project = session.get(Project, agent.project_id)
    workspace = project.workspace if project else None

    current = session.get(Task, agent.current_task_id) if agent.current_task_id else None

    # Latest progress / waiting / test signal for this agent.
    def latest(event_type: EventType) -> Event | None:
        return session.scalars(
            select(Event)
            .where(Event.agent_id == agent.id, Event.type == str(event_type))
            .order_by(Event.created_at.desc())
            .limit(1)
        ).first()

    progress_event = latest(EventType.AGENT_PROGRESS)
    waiting_event = latest(EventType.AGENT_WAITING)
    permission_event = latest(EventType.AGENT_PERMISSION_REQUIRED)

    test_event = session.scalars(
        select(Event)
        .where(Event.project_id == agent.project_id, Event.type == str(EventType.TEST_COMPLETED))
        .order_by(Event.created_at.desc())
        .limit(1)
    ).first()

    commits = session.scalars(
        select(Task).where(Task.agent_id == agent.id, Task.status == TaskStatus.SUCCEEDED)
    ).all()
    all_commits = [sha for task in commits for sha in (task.commits or [])]

    last_commit_event = session.scalars(
        select(Event)
        .where(Event.agent_id == agent.id, Event.type == str(EventType.COMMIT_CREATED))
        .order_by(Event.created_at.desc())
        .limit(1)
    ).first()
    files_changed = (
        int((last_commit_event.payload or {}).get("files_changed", 0)) if last_commit_event else 0
    )

    blocked_reason = None
    question = None
    if agent.status == AgentStatus.BLOCKED and waiting_event is not None:
        question = (waiting_event.payload or {}).get("question")
        blocked_reason = question
    elif agent.status == AgentStatus.AWAITING_APPROVAL and permission_event is not None:
        blocked_reason = (permission_event.payload or {}).get("command")
        question = (permission_event.payload or {}).get("reason")

    return AgentSummary(
        agent_id=agent.id,
        name=agent.name,
        status=agent.status,
        task=current.description if current else None,
        progress=(progress_event.payload or {}).get("message") if progress_event else None,
        files_changed=files_changed,
        branch=agent.branch,
        commits=all_commits,
        tests=(test_event.payload if test_event else None),
        blocked_reason=blocked_reason,
        question=question,
        workspace_url=workspace.code_server_url if workspace else None,
        updated_at=agent.updated_at,
    )
