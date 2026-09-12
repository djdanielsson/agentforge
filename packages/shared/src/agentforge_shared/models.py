"""Domain model.

Project -> Workspace (1:1), Project -> Agent (1:N), Agent -> Task (1:N).
Events are append-only so the dashboard can replay an agent's history.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base
from .enums import AgentStatus, Priority, ProjectStatus, TaskKind, TaskStatus, WorkspaceStatus


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    repository_url: Mapped[str | None] = mapped_column(String(500))
    default_branch: Mapped[str] = mapped_column(String(200), default="main")
    status: Mapped[str] = mapped_column(String(32), default=ProjectStatus.CREATING)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    workspace: Mapped[Workspace | None] = relationship(
        back_populates="project", uselist=False, cascade="all, delete-orphan"
    )
    agents: Mapped[list[Agent]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[Task]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Workspace(TimestampMixin, Base):
    """A project's isolated k3s world: one PVC + one pod, mounted at /workspace."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True, index=True
    )
    namespace: Mapped[str] = mapped_column(String(200))
    pvc_name: Mapped[str | None] = mapped_column(String(200))
    pod_name: Mapped[str | None] = mapped_column(String(200))
    service_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default=WorkspaceStatus.PENDING)
    provider: Mapped[str] = mapped_column(String(32), default="kubernetes")
    image: Mapped[str | None] = mapped_column(String(300))
    code_server_url: Mapped[str | None] = mapped_column(String(500))
    agent_server_url: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(Text)
    resources: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    project: Mapped[Project] = relationship(back_populates="workspace")


class Agent(TimestampMixin, Base):
    """One autonomous worker bound to a project and a git branch."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    model: Mapped[str] = mapped_column(String(200), default="local-coder")
    branch: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default=AgentStatus.STARTING)
    current_task_id: Mapped[str | None] = mapped_column(String(36))
    conversation: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    #: The AgentPermissions policy. Enforced by the workspace provider.
    policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: Human decisions on permission requests: request_id -> decision.
    permissions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    #: The OpenHands Agent Server session backing this agent, once started.
    session_id: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(Text)

    project: Mapped[Project] = relationship(back_populates="agents")
    tasks: Mapped[list[Task]] = relationship(back_populates="agent", cascade="all, delete-orphan")


class Task(TimestampMixin, Base):
    """A durable unit of work. Leased by exactly one orchestrator worker."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), default=TaskKind.IMPLEMENT)
    priority: Mapped[str] = mapped_column(String(16), default=Priority.NORMAL)
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.QUEUED, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    leased_by: Mapped[str | None] = mapped_column(String(200))
    leased_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    commits: Mapped[list[str]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="tasks")
    agent: Mapped[Agent | None] = relationship(back_populates="tasks")


class Event(Base):
    """Append-only agent/project timeline, streamed to the dashboard."""

    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(36), index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), index=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class ApiKey(Base):
    """A machine credential. Only the hash is stored; the plaintext is shown once.

    `scopes` gates what the key may do, and `project_id` optionally pins a key to
    a single project — so an automation credential cannot reach the whole fleet.
    """

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), index=True)  # for lookup
    key_hash: Mapped[str] = mapped_column(String(128), unique=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["read", "write"])
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    active: Mapped[bool] = mapped_column(default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Webhook(Base):
    """An outbound subscription. Hermes registers one of these and stops polling."""

    __tablename__ = "webhooks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    secret: Mapped[str] = mapped_column(String(200))  # HMAC-SHA256 signing key
    events: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["*"])
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WebhookDelivery(Base):
    """One attempt to deliver one event. Retries are driven off this table."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    webhook_id: Mapped[str] = mapped_column(
        ForeignKey("webhooks.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[str] = mapped_column(String(36), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    response_code: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SecretRef(Base):
    """A pointer to a credential, never the credential itself.

    The value lives in the workspace provider's own secret store (a Kubernetes
    Secret, a Podman secret). AgentForge stores only where to find it and which
    environment variable it should become. There is deliberately no column that
    can hold a secret value, so the API cannot leak one even by accident.
    """

    __tablename__ = "secret_refs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    #: global | project | agent
    scope: Mapped[str] = mapped_column(String(16), default="project", index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(36), index=True)
    #: Which workspace provider owns this secret: kubernetes | podman | external
    provider: Mapped[str] = mapped_column(String(32), default="kubernetes")
    #: The provider-native object name, e.g. the Kubernetes Secret's name.
    secret_name: Mapped[str] = mapped_column(String(253), nullable=False)
    #: The key inside that object.
    key: Mapped[str] = mapped_column(String(253), nullable=False)
    #: The environment variable it becomes inside the workspace.
    env_var: Mapped[str] = mapped_column(String(253), nullable=False)
    #: When false and the secret is missing, provisioning continues without it.
    required: Mapped[bool] = mapped_column(default=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
