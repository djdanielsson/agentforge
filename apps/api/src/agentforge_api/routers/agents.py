"""Agent endpoints — conversation, permissions, lifecycle."""

from __future__ import annotations

from agentforge_shared.enums import AgentStatus, EventType
from agentforge_shared.models import Agent
from agentforge_shared.permissions import AgentPermissions
from agentforge_shared.schemas import (
    AgentMessageIn,
    AgentRead,
    AgentUpdate,
    PermissionDecisionIn,
    PermissionsRead,
)
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..deps import DbSession
from ..services import record_event

router = APIRouter(prefix="/agents", tags=["agents"])


def _get_agent(session, agent_id: str) -> Agent:
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found")
    return agent


@router.get("", response_model=list[AgentRead])
def list_all_agents(session: DbSession, limit: int = 200) -> list[Agent]:
    return list(session.scalars(select(Agent).order_by(Agent.created_at.desc()).limit(limit)))


@router.get("/{agent_id}", response_model=AgentRead)
def get_agent(session: DbSession, agent_id: str) -> Agent:
    return _get_agent(session, agent_id)


@router.patch("/{agent_id}", response_model=AgentRead)
def update_agent(session: DbSession, agent_id: str, payload: AgentUpdate) -> Agent:
    agent = _get_agent(session, agent_id)
    for field, value in payload.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(agent, field, value)
    session.commit()
    session.refresh(agent)
    return agent


@router.post("/{agent_id}/restart", response_model=AgentRead)
def restart_agent(session: DbSession, agent_id: str) -> Agent:
    """Drop the agent's OpenHands session and let the orchestrator start a new one.

    The workspace is untouched: restarting an agent must not throw away the work
    in /workspace.
    """
    agent = _get_agent(session, agent_id)
    agent.session_id = None
    agent.current_task_id = None
    agent.error = None
    agent.status = AgentStatus.STARTING
    record_event(
        session,
        type=EventType.AGENT_STATUS,
        project_id=agent.project_id,
        agent_id=agent.id,
        payload={"status": str(AgentStatus.STARTING), "reason": "restart requested"},
    )
    session.commit()
    session.refresh(agent)
    return agent


@router.get("/{agent_id}/permissions", response_model=PermissionsRead)
def get_permissions(session: DbSession, agent_id: str) -> PermissionsRead:
    """The effective policy, with defaults filled in.

    A caller needs to know what the agent may actually do, not what was stored.
    """
    agent = _get_agent(session, agent_id)
    policy = AgentPermissions.from_dict(agent.policy)
    return PermissionsRead(agent_id=agent.id, policy=policy.model_dump())


@router.post("/{agent_id}/stop", response_model=AgentRead)
def stop_agent(session: DbSession, agent_id: str) -> Agent:
    agent = _get_agent(session, agent_id)
    if agent.status == AgentStatus.STOPPED:
        return agent
    agent.status = AgentStatus.STOPPED
    record_event(
        session,
        type=EventType.AGENT_STATUS,
        project_id=agent.project_id,
        agent_id=agent.id,
        payload={"status": str(AgentStatus.STOPPED)},
    )
    session.commit()
    session.refresh(agent)
    return agent


@router.post("/{agent_id}/messages", response_model=AgentRead, status_code=status.HTTP_202_ACCEPTED)
def post_message(session: DbSession, agent_id: str, payload: AgentMessageIn) -> Agent:
    """Record a human turn. The orchestrator forwards it to the agent runtime."""
    agent = _get_agent(session, agent_id)
    conversation = list(agent.conversation or [])
    conversation.append({"role": "user", "content": payload.content})
    agent.conversation = conversation
    agent.status = AgentStatus.WORKING
    record_event(
        session,
        type=EventType.AGENT_MESSAGE,
        project_id=agent.project_id,
        agent_id=agent.id,
        payload={"role": "user", "content": payload.content},
    )
    session.commit()
    session.refresh(agent)
    return agent


@router.get("/{agent_id}/conversation")
def get_conversation(session: DbSession, agent_id: str) -> dict:
    agent = _get_agent(session, agent_id)
    return {"agent_id": agent.id, "messages": agent.conversation or []}


@router.post("/{agent_id}/permissions", response_model=AgentRead)
def decide_permission(session: DbSession, agent_id: str, payload: PermissionDecisionIn) -> Agent:
    """Answer a blocked agent's permission request."""
    agent = _get_agent(session, agent_id)
    permissions = dict(agent.permissions or {})
    permissions[payload.request_id] = payload.decision
    agent.permissions = permissions
    agent.status = AgentStatus.WORKING if payload.decision != "deny" else AgentStatus.IDLE
    granted = payload.decision != "deny"
    record_event(
        session,
        type=EventType.PERMISSION_GRANTED if granted else EventType.PERMISSION_DENIED,
        project_id=agent.project_id,
        agent_id=agent.id,
        payload={"request_id": payload.request_id, "decision": payload.decision},
    )
    session.commit()
    session.refresh(agent)
    return agent
