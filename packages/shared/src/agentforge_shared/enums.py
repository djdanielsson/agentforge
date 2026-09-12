"""Lifecycle states and the canonical event catalog.

Every state change worth reacting to is one of these event types. The catalog is
the public contract: webhooks, the WebSocket stream and the CLI all speak it, so
adding a subscriber never requires touching the emitter.
"""

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
    DESTROYED = "destroyed"


class AgentStatus(StrEnum):
    STARTING = "starting"
    IDLE = "idle"
    WORKING = "working"
    BLOCKED = "blocked"          # waiting on a human answer
    AWAITING_APPROVAL = "awaiting_approval"
    STOPPED = "stopped"
    ERROR = "error"


class TaskStatus(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"            # claimed by a worker, not yet running
    RUNNING = "running"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskKind(StrEnum):
    """What kind of work a task represents.

    IMPLEMENT is the default agent turn. The others are semantic actions the
    API exposes directly (review / test / deploy) so a caller never has to
    know how the work is actually performed.
    """

    IMPLEMENT = "implement"
    REVIEW = "review"
    TEST = "test"
    DEPLOY = "deploy"
    MAINTAIN = "maintain"        # scheduled/automation work


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class EventType(StrEnum):
    """The wire contract for everything that happens."""

    # projects
    PROJECT_CREATED = "project.created"
    PROJECT_STATUS = "project.status"

    # workspaces
    WORKSPACE_CREATED = "workspace.created"
    WORKSPACE_READY = "workspace.ready"
    WORKSPACE_DESTROYED = "workspace.destroyed"
    WORKSPACE_FAILED = "workspace.failed"

    # agents
    AGENT_CREATED = "agent.created"
    AGENT_STARTED = "agent.started"
    AGENT_PROGRESS = "agent.progress"
    AGENT_MESSAGE = "agent.message"
    AGENT_WAITING = "agent.waiting"
    AGENT_PERMISSION_REQUIRED = "agent.permission_required"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"
    AGENT_STOPPED = "agent.stopped"
    AGENT_STATUS = "agent.status"

    # permissions
    PERMISSION_GRANTED = "permission.granted"
    PERMISSION_DENIED = "permission.denied"

    # tasks
    TASK_CREATED = "task.created"
    TASK_STATUS = "task.status"

    # outcomes
    COMMIT_CREATED = "commit.created"
    TEST_COMPLETED = "test.completed"
    REVIEW_COMPLETED = "review.completed"
    DEPLOY_COMPLETED = "deploy.completed"


class PermissionDecision(StrEnum):
    ALLOW_ONCE = "allow_once"
    ALLOW_PROJECT = "allow_project"
    DENY = "deny"


#: Event types a webhook may subscribe to. `*` matches everything.
EVENT_WILDCARD = "*"
