"""The durable concepts of the platform (SPEC §51).

Project -> Workspace -> Agent -> Task, plus Credential, Usage and Event. These
tables are deliberately independent of DevPod, T3 Code, OpenCode and LiteLLM:
those are components, not the model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, onupdate=now
    )


class Project(Base, TimestampMixin):
    """An independently managed software project (SPEC §7)."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("prj"))
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")

    # repository
    repository_url: Mapped[str] = mapped_column(String(512), default="")
    repository_provider: Mapped[str] = mapped_column(String(32), default="github")
    repository_branch: Mapped[str] = mapped_column(String(128), default="main")

    # workspace
    workspace_provider: Mapped[str] = mapped_column(String(32), default="")
    cpu: Mapped[str] = mapped_column(String(16), default="500m")
    memory: Mapped[str] = mapped_column(String(16), default="1Gi")
    storage: Mapped[str] = mapped_column(String(16), default="5Gi")

    # policy (SPEC §14); stored as an opaque document the LLM layer interprets
    llm_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    policies: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    workspaces: Mapped[list[Workspace]] = relationship(back_populates="project")
    agents: Mapped[list[Agent]] = relationship(back_populates="project")


class Workspace(Base, TimestampMixin):
    """One isolated environment belonging to a project (SPEC §5, §18)."""

    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("wsp"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="default")

    provider: Mapped[str] = mapped_column(String(32), default="")
    # `reference` is the provider's handle: for Kubernetes providers it is the
    # namespace, which is also the unit of isolation.
    reference: Mapped[str] = mapped_column(String(128), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error: Mapped[str] = mapped_column(Text, default="")

    pod_name: Mapped[str] = mapped_column(String(128), default="")
    pvc_name: Mapped[str] = mapped_column(String(128), default="")
    service_name: Mapped[str] = mapped_column(String(128), default="")
    t3_ingress_host: Mapped[str] = mapped_column(String(255), default="")

    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    project: Mapped[Project] = relationship(back_populates="workspaces")
    agents: Mapped[list[Agent]] = relationship(back_populates="workspace")


class Agent(Base, TimestampMixin):
    """A provider-neutral AI coding worker (SPEC §8)."""

    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("agt"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    workspace_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id"), nullable=True)

    name: Mapped[str] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(32), default="opencode")
    role: Mapped[str] = mapped_column(String(128), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="created")
    current_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    worktree: Mapped[str] = mapped_column(String(255), default="")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    project: Mapped[Project] = relationship(back_populates="agents")
    workspace: Mapped[Workspace | None] = relationship(back_populates="agents")


class Task(Base, TimestampMixin):
    """Work assigned to an agent (SPEC §19, §20)."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("tsk"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    result: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # git outcome (SPEC §20 "eventually")
    git_branch: Mapped[str] = mapped_column(String(255), default="")
    git_commit: Mapped[str] = mapped_column(String(64), default="")
    worktree: Mapped[str] = mapped_column(String(255), default="")


class Event(Base):
    """The append-only activity log (SPEC §21, §33)."""

    __tablename__ = "events"

    # A monotonic integer id doubles as the SSE cursor: clients reconnect with
    # `?since=<id>` and never need to reconcile timestamps.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    type: Mapped[str] = mapped_column(String(64), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class UsageRecord(Base, TimestampMixin):
    """LLM usage attributed to a project, agent and task (SPEC §13)."""

    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(String(64), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    provider: Mapped[str] = mapped_column(String(64), default="gateway")
    model: Mapped[str] = mapped_column(String(64), default="")
    requested_model: Mapped[str] = mapped_column(String(64), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(default=0.0)


class Credential(Base):
    """Credential *metadata*. Values live in Kubernetes Secrets (SPEC §16)."""

    __tablename__ = "credentials"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: new_id("crd"))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    secret_name: Mapped[str] = mapped_column(String(255))
    key: Mapped[str] = mapped_column(String(128), default="token")
    env_var: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # SPEC §17: scopes beyond project scope.
    allowed_agents: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
