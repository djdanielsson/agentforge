"""Business logic shared by the routers.

The API writes *desired* state and records events. It never provisions a
workspace itself — the orchestrator reconciles that asynchronously.
"""

from __future__ import annotations

import re
import unicodedata

from agentforge_shared.config import get_settings
from agentforge_shared.enums import (
    AgentStatus,
    EventType,
    Priority,
    ProjectStatus,
    TaskKind,
    TaskStatus,
    WorkspaceStatus,
)
from agentforge_shared.events import record_event
from agentforge_shared.models import Agent, Project, Task, Workspace
from agentforge_shared.schemas import AgentCreate, ProjectCreate
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    normalised = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = _SLUG_STRIP.sub("-", normalised.lower()).strip("-")
    return slug or "project"


def unique_slug(session: Session, base: str) -> str:
    slug, n = base, 2
    while session.scalar(select(func.count()).select_from(Project).where(Project.slug == slug)):
        slug = f"{base}-{n}"
        n += 1
    return slug


def create_project(session: Session, payload: ProjectCreate) -> Project:
    project = Project(
        name=payload.name,
        slug=unique_slug(session, slugify(payload.name)),
        description=payload.description,
        repository_url=payload.repository_url,
        default_branch=payload.default_branch,
        status=ProjectStatus.CREATING,
        settings=payload.settings,
    )
    session.add(project)
    session.flush()  # assign project.id

    # A workspace row is created immediately in PENDING; the orchestrator fills
    # in namespace/pod names once the k8s objects exist.
    # Namespace prefix comes from settings so a deployment can namespace-isolate
    # itself; the slug makes it recognisable in `kubectl get ns`.
    prefix = get_settings().workspace_namespace_prefix
    workspace = Workspace(
        project_id=project.id,
        namespace=f"{prefix}-{project.slug}"[:60],
        status=WorkspaceStatus.PENDING,
    )
    session.add(workspace)
    record_event(
        session,
        type=EventType.PROJECT_CREATED,
        project_id=project.id,
        payload={"name": project.name, "repository_url": project.repository_url},
    )
    session.commit()
    session.refresh(project)
    return project


def create_agent(session: Session, project: Project, payload: AgentCreate) -> Agent:
    default_model = (project.settings or {}).get("default_model", "local-coder")
    agent = Agent(
        project_id=project.id,
        workspace_id=project.workspace.id if project.workspace else None,
        name=payload.name,
        model=payload.model or default_model,
        branch=payload.branch or f"agent/{slugify(payload.name)}",
        status=AgentStatus.STARTING,
    )
    session.add(agent)
    session.flush()
    record_event(
        session,
        type=EventType.AGENT_CREATED,
        project_id=project.id,
        agent_id=agent.id,
        payload={"name": agent.name, "model": agent.model, "branch": agent.branch},
    )
    session.commit()
    session.refresh(agent)
    return agent


def create_task(
    session: Session,
    project: Project,
    description: str,
    agent_id: str | None,
    *,
    kind: TaskKind = TaskKind.IMPLEMENT,
    priority: Priority = Priority.NORMAL,
    payload: dict | None = None,
) -> Task:
    next_position = (
        session.scalar(
            select(func.coalesce(func.max(Task.position), 0)).where(Task.project_id == project.id)
        )
        or 0
    ) + 1

    task = Task(
        project_id=project.id,
        agent_id=agent_id,
        description=description,
        kind=str(kind),
        priority=str(priority),
        payload=payload or {},
        status=TaskStatus.QUEUED,
        position=next_position,
    )
    session.add(task)
    session.flush()
    record_event(
        session,
        type=EventType.TASK_CREATED,
        project_id=project.id,
        agent_id=agent_id,
        task_id=task.id,
        payload={"description": description, "kind": str(task.kind),
                 "priority": str(task.priority)},
    )
    session.commit()
    session.refresh(task)
    return task
