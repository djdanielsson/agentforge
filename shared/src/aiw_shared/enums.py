"""Lifecycle states. Plain str enums so they serialise cleanly to JSON/SQL."""

from __future__ import annotations

from enum import StrEnum


class ProjectStatus(StrEnum):
    CREATING = "creating"
    READY = "ready"
    ERROR = "error"
    ARCHIVED = "archived"


class WorkspaceStatus(StrEnum):
    PENDING = "pending"
    PROVISIONING = "provisioning"
    CLONING = "cloning"
    READY = "ready"
    STOPPED = "stopped"
    ERROR = "error"
    DELETING = "deleting"


class AgentStatus(StrEnum):
    STARTING = "starting"
    IDLE = "idle"
    WORKING = "working"
    BLOCKED = "blocked"  # waiting on a human answer
    AWAITING_APPROVAL = "awaiting_approval"
    STOPPED = "stopped"
    ERROR = "error"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"  # claimed by a worker, not yet running
    RUNNING = "running"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventType(StrEnum):
    PROJECT_CREATED = "project.created"
    PROJECT_STATUS = "project.status"
    WORKSPACE_STATUS = "workspace.status"
    AGENT_CREATED = "agent.created"
    AGENT_STATUS = "agent.status"
    AGENT_MESSAGE = "agent.message"
    AGENT_QUESTION = "agent.question"  # agent blocked, needs a human
    PERMISSION_REQUEST = "agent.permission_request"
    TASK_CREATED = "task.created"
    TASK_STATUS = "task.status"
    TASK_OUTPUT = "task.output"
    GIT_COMMIT = "git.commit"


class PermissionDecision(StrEnum):
    ALLOW_ONCE = "allow_once"
    ALLOW_PROJECT = "allow_project"
    DENY = "deny"
