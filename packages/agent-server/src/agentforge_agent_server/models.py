"""Types exchanged with the Agent Server.

Deliberately a small, stable vocabulary. OpenHands models its own richer
concepts; we translate at the boundary rather than leaking them upward.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ConversationSpec:
    """What we ask OpenHands for.

    OpenHands 0.59 has no separate "agent" object: a conversation *is* the agent
    session, and the model is a server-level setting rather than a per-agent
    field, which is why the alias is configured separately (see
    `AgentServerClient.configure_model`).
    """

    repository: str | None = None
    branch: str | None = None
    #: Free-form framing handed to the agent at the start of the conversation.
    instructions: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentRun:
    """A conversation as the server reports it."""

    session_id: str
    status: str = "running"
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentEvent:
    """One thing the agent did or said.

    OpenHands emits a stream of actions and observations; this keeps only what
    the control plane needs to turn a turn into AgentForge events.
    """

    type: str
    session_id: str | None = None
    content: str | None = None
    #: True when the agent has stopped and needs a human.
    blocked: bool = False
    question: str | None = None
    #: A command the agent wants to run that needs approval.
    permission_request: dict[str, Any] | None = None
    finished: bool = False
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> AgentEvent:
        """Tolerantly map a server payload onto our vocabulary.

        OpenHands event key names have moved between releases (`type` vs `event`,
        `message` vs `content`) and an action carries its payload under `action`
        while an observation carries it under `observation`. Reading all of them
        is what keeps a version bump from looking like an agent that went quiet.
        """
        action = payload.get("action") if isinstance(payload.get("action"), dict) else None
        observation = (
            payload.get("observation") if isinstance(payload.get("observation"), dict) else None
        )
        content = (
            payload.get("content")
            or payload.get("message")
            or payload.get("thought")
            or (action or {}).get("message")
            or (observation or {}).get("content")
            or (observation or {}).get("message")
        )
        state = str(payload.get("state") or "")
        kind = payload.get("type") or payload.get("event") or payload.get("source") or "unknown"
        return cls(
            type=str(kind),
            session_id=payload.get("session_id")
            or payload.get("sessionId")
            or payload.get("conversation_id"),
            content=content,
            blocked=bool(payload.get("blocked") or payload.get("waiting_for_user")),
            question=payload.get("question"),
            permission_request=payload.get("permission_request"),
            finished=bool(payload.get("finished") or payload.get("done"))
            or state in ("finished", "stopped"),
            error=payload.get("error"),
            raw=payload,
        )


@dataclass(slots=True)
class FileEntry:
    path: str
    is_dir: bool = False
    size: int | None = None
