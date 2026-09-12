"""Types exchanged with the Agent Server.

Deliberately a small, stable vocabulary. OpenHands models its own richer
concepts; we translate at the boundary rather than leaking them upward.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class WorkspaceRef:
    """A workspace as the Agent Server sees it."""

    id: str
    path: str = "/workspace"
    status: str = "unknown"
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentSpec:
    """What we ask the Agent Server to create."""

    name: str
    model: str
    workspace_id: str | None = None
    system_prompt: str | None = None
    #: Passed through so the server can honour the same restrictions we set on
    #: the pod. Belt and braces: the pod is the real boundary.
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentRun:
    """A conversation turn or task handed to an agent."""

    session_id: str
    status: str = "running"
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentEvent:
    """One thing the agent did or said.

    The shape mirrors what AgentForge emits, so translating an Agent Server
    event into an AgentForge event is a rename rather than a redesign.
    """

    type: str
    session_id: str | None = None
    content: str | None = None
    #: True when the agent has stopped and is waiting for a human.
    blocked: bool = False
    question: str | None = None
    #: A command the agent wants to run that needs approval.
    permission_request: dict[str, Any] | None = None
    finished: bool = False
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> AgentEvent:
        """Tolerantly map a server payload onto our vocabulary."""
        return cls(
            type=str(payload.get("type") or payload.get("event") or "unknown"),
            session_id=payload.get("session_id") or payload.get("sessionId"),
            content=payload.get("content") or payload.get("message"),
            blocked=bool(payload.get("blocked") or payload.get("waiting_for_user")),
            question=payload.get("question"),
            permission_request=payload.get("permission_request"),
            finished=bool(payload.get("finished") or payload.get("done")),
            error=payload.get("error"),
            raw=payload,
        )


@dataclass(slots=True)
class FileEntry:
    path: str
    is_dir: bool = False
    size: int | None = None
