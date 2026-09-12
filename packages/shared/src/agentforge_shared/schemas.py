"""Pydantic schemas — the wire contract for the API and the dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import (
    AgentStatus,
    Priority,
    ProjectStatus,
    TaskKind,
    TaskStatus,
    WorkspaceStatus,
)


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
    provider: str = "kubernetes"
    image: str | None = None
    code_server_url: str | None = None
    agent_server_url: str | None = None
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
    #: Omit to get the restrictive default policy.
    policy: dict[str, Any] | None = None


class AgentUpdate(BaseModel):
    name: str | None = None
    model: str | None = None
    branch: str | None = None
    status: AgentStatus | None = None
    policy: dict[str, Any] | None = None


class AgentRead(ORMModel):
    id: str
    project_id: str
    workspace_id: str | None = None
    name: str
    model: str
    branch: str | None = None
    status: AgentStatus
    current_task_id: str | None = None
    policy: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class FileWriteRequest(BaseModel):
    """A write from the editor. The path is resolved inside the workspace."""

    path: str = Field(min_length=1)
    content: str
    #: "utf8" for text, "base64" to round-trip a file the editor read as binary.
    encoding: str = "utf8"


class AgentMessageIn(BaseModel):
    """A human turn in the agent conversation."""

    content: str = Field(min_length=1)


class PermissionDecisionIn(BaseModel):
    decision: str = Field(pattern="^(allow_once|allow_project|deny)$")
    request_id: str


# --- tasks ------------------------------------------------------------------


class TaskCreate(BaseModel):
    """Semantic work request.

    `prompt` is the high-level form a caller like Hermes uses; `description` is
    kept as an alias so the two never diverge.
    """

    prompt: str | None = None
    description: str | None = None
    agent_id: str | None = None
    kind: TaskKind = TaskKind.IMPLEMENT
    priority: Priority = Priority.NORMAL
    branch: str | None = None

    @model_validator(mode="after")
    def _require_text(self) -> TaskCreate:
        if not (self.prompt or self.description or "").strip():
            raise ValueError("either `prompt` or `description` is required")
        return self

    def resolved_description(self) -> str:
        """The text the agent actually receives."""
        return (self.prompt or self.description or "").strip()


class ReviewRequest(BaseModel):
    branch: str | None = None
    base: str | None = None
    agent_id: str | None = None


class TestRequest(BaseModel):
    branch: str | None = None
    command: str | None = None
    agent_id: str | None = None


class DeployRequest(BaseModel):
    branch: str | None = None
    environment: str = "staging"
    agent_id: str | None = None


class TaskRead(ORMModel):
    id: str
    project_id: str
    agent_id: str | None = None
    description: str
    kind: TaskKind = TaskKind.IMPLEMENT
    priority: Priority = Priority.NORMAL
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


# --- api keys ---------------------------------------------------------------


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    scopes: list[str] = Field(default_factory=lambda: ["read", "write"])
    project_id: str | None = None


class ApiKeyRead(ORMModel):
    id: str
    name: str
    prefix: str
    scopes: list[str]
    project_id: str | None = None
    active: bool
    last_used_at: datetime | None = None
    created_at: datetime


class ApiKeyCreated(ApiKeyRead):
    """Returned exactly once, at creation. The plaintext is never stored."""

    key: str


# --- webhooks ---------------------------------------------------------------


class WebhookCreate(BaseModel):
    url: str = Field(min_length=1, max_length=1000)
    description: str | None = None
    events: list[str] = Field(default_factory=lambda: ["*"])
    project_id: str | None = None
    secret: str | None = None  # generated when omitted


class WebhookUpdate(BaseModel):
    url: str | None = None
    description: str | None = None
    events: list[str] | None = None
    active: bool | None = None


class WebhookRead(ORMModel):
    id: str
    url: str
    description: str | None = None
    events: list[str]
    project_id: str | None = None
    active: bool
    created_at: datetime
    updated_at: datetime


class WebhookCreated(WebhookRead):
    """Returned once so the caller can store the signing secret."""

    secret: str


class WebhookDeliveryRead(ORMModel):
    id: str
    webhook_id: str
    event_id: str
    status: str
    attempts: int
    response_code: int | None = None
    error: str | None = None
    delivered_at: datetime | None = None
    created_at: datetime


# --- agent status summary ---------------------------------------------------


class AgentSummary(BaseModel):
    """The shape a conversational caller wants: what is it doing, is it stuck."""

    agent_id: str
    name: str
    status: AgentStatus
    task: str | None = None
    progress: str | None = None
    files_changed: int = 0
    branch: str | None = None
    commits: list[str] = Field(default_factory=list)
    tests: dict[str, Any] | None = None
    blocked_reason: str | None = None
    question: str | None = None
    workspace_url: str | None = None
    updated_at: datetime | None = None


class EventTypeInfo(BaseModel):
    """One entry in the event catalog, so clients can discover subscriptions."""

    type: str
    description: str
    payload_fields: list[str] = Field(default_factory=list)


# --- secret references ------------------------------------------------------


class SecretRefCreate(BaseModel):
    """Register a pointer to a credential.

    Note there is no `value` field. The secret itself must already exist in the
    workspace provider's secret store; this only says where to find it and what
    env var it should become.
    """

    name: str = Field(min_length=1, max_length=200)
    scope: str = Field(default="project", pattern="^(global|project|agent)$")
    secret_name: str = Field(min_length=1, max_length=253)
    key: str = Field(min_length=1, max_length=253)
    env_var: str = Field(min_length=1, max_length=253, pattern="^[A-Z][A-Z0-9_]*$")
    provider: str = Field(default="kubernetes", pattern="^(kubernetes|podman|external)$")
    required: bool = False
    description: str | None = None
    project_id: str | None = None
    agent_id: str | None = None


class SecretRefRead(ORMModel):
    id: str
    name: str
    scope: str
    project_id: str | None = None
    agent_id: str | None = None
    provider: str
    secret_name: str
    key: str
    env_var: str
    required: bool
    description: str | None = None
    created_at: datetime
    updated_at: datetime


class ResolvedSecret(BaseModel):
    """What the provider actually receives. Still no value."""

    env_var: str
    secret_name: str
    key: str
    required: bool = False


# --- permissions ------------------------------------------------------------


class PermissionsRead(BaseModel):
    """The effective policy, with the defaults filled in."""

    agent_id: str
    policy: dict[str, Any]


# --- workspace lifecycle ----------------------------------------------------


class WorkspaceActionAccepted(BaseModel):
    project_id: str
    action: str
    status: str
    detail: str | None = None
