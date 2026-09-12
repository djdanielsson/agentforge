"""Pydantic schemas — the wire contract for the API and the dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import AgentStatus, ProjectStatus, TaskStatus, WorkspaceStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- projects ---------------------------------------------------------------


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    repository_url: str | None = None
    default_branch: str = "main"
    description: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    default_branch: str | None = None
    status: ProjectStatus | None = None
    settings: dict[str, Any] | None = None


class WorkspaceRead(ORMModel):
    id: str
    project_id: str
    namespace: str
    pvc_name: str | None = None
    pod_name: str | None = None
    service_name: str | None = None
    status: WorkspaceStatus
    image: str | None = None
    code_server_url: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class ProjectRead(ORMModel):
    id: str
    name: str
    slug: str
    description: str | None = None
    repository_url: str | None = None
    default_branch: str
    status: ProjectStatus
    settings: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ProjectDetail(ProjectRead):
    workspace: WorkspaceRead | None = None
    agents: list[AgentRead] = Field(default_factory=list)


# --- agents -----------------------------------------------------------------


class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    model: str | None = None
    branch: str | None = None


class AgentUpdate(BaseModel):
    name: str | None = None
    model: str | None = None
    branch: str | None = None
    status: AgentStatus | None = None


class AgentRead(ORMModel):
    id: str
    project_id: str
    workspace_id: str | None = None
    name: str
    model: str
    branch: str | None = None
    status: AgentStatus
    current_task_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentMessageIn(BaseModel):
    """A human turn in the agent conversation."""

    content: str = Field(min_length=1)


class PermissionDecisionIn(BaseModel):
    decision: str = Field(pattern="^(allow_once|allow_project|deny)$")
    request_id: str


# --- tasks ------------------------------------------------------------------


class TaskCreate(BaseModel):
    description: str = Field(min_length=1)
    agent_id: str | None = None


class TaskRead(ORMModel):
    id: str
    project_id: str
    agent_id: str | None = None
    description: str
    status: TaskStatus
    position: int = 0
    attempts: int
    result: str | None = None
    error: str | None = None
    commits: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


# --- events -----------------------------------------------------------------


class EventRead(ORMModel):
    id: str
    project_id: str | None = None
    agent_id: str | None = None
    task_id: str | None = None
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# --- git --------------------------------------------------------------------


class GitFileChange(BaseModel):
    path: str
    status: str  # added | modified | deleted | renamed
    additions: int = 0
    deletions: int = 0


class GitDiff(BaseModel):
    branch: str
    base: str
    diff: str
    files: list[GitFileChange] = Field(default_factory=list)


class GitCommitRequest(BaseModel):
    message: str = Field(min_length=1)
    paths: list[str] = Field(default_factory=list)


class GitMergeRequest(BaseModel):
    source_branch: str
    target_branch: str | None = None
    strategy: str = "merge"


ProjectDetail.model_rebuild()
