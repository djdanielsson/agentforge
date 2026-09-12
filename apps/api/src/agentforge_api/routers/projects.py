"""Project + workspace endpoints."""

from __future__ import annotations

from agentforge_shared.enums import EventType, ProjectStatus, WorkspaceStatus
from agentforge_shared.models import Project, Workspace
from agentforge_shared.schemas import (
    AgentCreate,
    AgentRead,
    ProjectCreate,
    ProjectDetail,
    ProjectRead,
    ProjectUpdate,
    WorkspaceRead,
)
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from ..deps import DbSession
from ..security import Principal, ensure_project_access, get_principal, require_write
from ..services import create_agent, create_project, record_event

router = APIRouter(prefix="/projects", tags=["projects"])


def _get_project(session, project_id: str, principal: Principal) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    ensure_project_access(principal, project.id)
    return project


@router.get("", response_model=list[ProjectRead])
def list_projects(
    session: DbSession,
    limit: int = 100,
    offset: int = 0,
    principal: Principal = Depends(get_principal),
) -> list[Project]:
    stmt = select(Project).order_by(Project.created_at.desc()).limit(limit).offset(offset)
    if principal.project_id:
        stmt = stmt.where(Project.id == principal.project_id)
    return list(session.scalars(stmt))


@router.post("", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
def create(
    session: DbSession,
    payload: ProjectCreate,
    principal: Principal = Depends(require_write),
) -> Project:
    return create_project(session, payload)


@router.get("/{project_id}", response_model=ProjectDetail)
def get_project(
    session: DbSession, project_id: str, principal: Principal = Depends(get_principal)
) -> Project:
    return _get_project(session, project_id, principal)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(
    session: DbSession,
    project_id: str,
    payload: ProjectUpdate,
    principal: Principal = Depends(require_write),
) -> Project:
    project = _get_project(session, project_id, principal)
    for field, value in payload.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(project, field, value)
    session.commit()
    session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    session: DbSession, project_id: str, principal: Principal = Depends(require_write)
) -> None:
    """Marks the project archived; the orchestrator tears the workspace down."""
    project = _get_project(session, project_id, principal)
    project.status = ProjectStatus.ARCHIVED
    if project.workspace:
        project.workspace.status = WorkspaceStatus.DELETING
    record_event(
        session,
        type=EventType.PROJECT_STATUS,
        project_id=project.id,
        payload={"status": str(ProjectStatus.ARCHIVED)},
    )
    session.commit()


@router.get("/{project_id}/workspace", response_model=WorkspaceRead)
def get_workspace(
    session: DbSession, project_id: str, principal: Principal = Depends(get_principal)
) -> Workspace:
    project = _get_project(session, project_id, principal)
    if project.workspace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace not provisioned")
    return project.workspace


@router.post("/{project_id}/agents", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
def add_agent(
    session: DbSession,
    project_id: str,
    payload: AgentCreate,
    principal: Principal = Depends(require_write),
):
    project = _get_project(session, project_id, principal)
    return create_agent(session, project, payload)


@router.get("/{project_id}/agents", response_model=list[AgentRead])
def list_agents(
    session: DbSession, project_id: str, principal: Principal = Depends(get_principal)
):
    project = _get_project(session, project_id, principal)
    return sorted(project.agents, key=lambda a: a.created_at)


@router.get("/{project_id}/agents/{agent_id}/status", response_model=AgentRead)
def agent_status(
    session: DbSession,
    project_id: str,
    agent_id: str,
    principal: Principal = Depends(get_principal),
):
    project = _get_project(session, project_id, principal)
    for agent in project.agents:
        if agent.id == agent_id:
            return agent
    raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
