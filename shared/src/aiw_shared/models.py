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
from .enums import AgentStatus, ProjectStatus, TaskStatus, WorkspaceStatus


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
    image: Mapped[str | None] = mapped_column(String(300))
    code_server_url: Mapped[str | None] = mapped_column(String(500))
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
    permissions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
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
    status: Mapped[str] = mapped_column(String(32), default=TaskStatus.QUEUED, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
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
