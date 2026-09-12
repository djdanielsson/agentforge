"""Agent lifecycle and task dispatch.

Take a queued task, hand it to the project's agent runtime, stream the result
back as events, and write the commits it produced.
"""

from __future__ import annotations

import logging

from aiw_shared.config import get_settings
from aiw_shared.db import session_scope
from aiw_shared.enums import AgentStatus, EventType, TaskStatus, WorkspaceStatus
from aiw_shared.models import Agent, Event, Project, Task, Workspace
from sqlalchemy import select

from .openhands import OpenHandsClient

log = logging.getLogger(__name__)


class AgentManager:
    def __init__(self, client: OpenHandsClient | None = None) -> None:
        self.settings = get_settings()
        self.client = client or OpenHandsClient(self.settings.openhands_url)

    def runnable_agents(self) -> list[Agent]:
        with session_scope() as session:
            agents = session.scalars(
                select(Agent).where(
                    Agent.status.in_([AgentStatus.STARTING, AgentStatus.IDLE, AgentStatus.WORKING])
                )
            ).all()
            for agent in agents:
                session.expunge(agent)
            return list(agents)

    def workspace_for(self, agent: Agent) -> Workspace | None:
        with session_scope() as session:
            return session.scalars(
                select(Workspace).where(Workspace.project_id == agent.project_id)
            ).first()

    def dispatch(self, agent: Agent, task: Task) -> None:
        """Send one task to the agent and record the outcome."""
        workspace = self.workspace_for(agent)
        if workspace is None or workspace.status != WorkspaceStatus.READY:
            log.info("agent %s has no ready workspace; skipping", agent.name)
            return

        self._set_agent(agent.id, AgentStatus.WORKING, current_task_id=task.id)
        self._event(
            agent, EventType.TASK_STATUS, task=task, payload={"status": str(TaskStatus.RUNNING)}
        )

        try:
            session_id = self._ensure_session(agent, workspace)
            reply = self.client.send_message(session_id=session_id, content=task.description)
        except Exception as exc:  # noqa: BLE001 - runtime is external
            log.exception("agent %s failed task %s", agent.name, task.id)
            self._event(agent, EventType.TASK_OUTPUT, task=task, payload={"error": str(exc)})
            self._set_agent(agent.id, AgentStatus.ERROR, error=str(exc)[:2000])
            with session_scope() as session:
                row = session.get(Task, task.id)
                row.status = TaskStatus.FAILED
                row.error = str(exc)[:2000]
            return

        self._append_message(agent.id, "assistant", reply.content)
        self._event(
            agent,
            EventType.AGENT_MESSAGE,
            task=task,
            payload={"role": "assistant", "content": reply.content},
        )

        if reply.permission_request:
            self._set_agent(agent.id, AgentStatus.AWAITING_APPROVAL)
            self._event(
                agent, EventType.PERMISSION_REQUEST, task=task, payload=reply.permission_request
            )
            with session_scope() as session:
                row = session.get(Task, task.id)
                row.status = TaskStatus.BLOCKED
            return

        if reply.blocked:
            self._set_agent(agent.id, AgentStatus.BLOCKED)
            self._event(
                agent,
                EventType.AGENT_QUESTION,
                task=task,
                payload={"question": reply.question or reply.content},
            )
            with session_scope() as session:
                row = session.get(Task, task.id)
                row.status = TaskStatus.BLOCKED
                row.result = reply.question or reply.content
            return

        commits = self._commit_if_dirty(agent, workspace, task)
        with session_scope() as session:
            row = session.get(Task, task.id)
            row.status = TaskStatus.SUCCEEDED
            row.result = reply.content
            row.commits = commits

        self._set_agent(agent.id, AgentStatus.IDLE, clear_task=True)

    # --- helpers ----------------------------------------------------------

    def _ensure_session(self, agent: Agent, workspace: Workspace) -> str:
        if agent.current_task_id and agent.permissions.get("session_id"):
            return agent.permissions["session_id"]
        started = self.client.start_agent(
            agent_id=agent.id,
            workspace_url=workspace.code_server_url or "",
            model=agent.model,
        )
        session_id = started.get("session_id") or started.get("id") or agent.id
        with session_scope() as session:
            row = session.get(Agent, agent.id)
            perms = dict(row.permissions or {})
            perms["session_id"] = session_id
            row.permissions = perms
        return session_id

    def _commit_if_dirty(self, agent: Agent, workspace: Workspace, task: Task) -> list[str]:
        from .git_manager import GitManager

        if not workspace.pod_name:
            return []
        git = GitManager(workspace.namespace, workspace.pod_name)
        try:
            state = git.state()
        except Exception as exc:  # noqa: BLE001
            log.warning("git state unavailable for %s: %s", workspace.namespace, exc)
            return []
        if state.clean:
            return []
        git.ensure_branch(agent.branch or f"agent/{agent.id}")
        sha = git.commit_all(f"{agent.name}: {task.description[:60]}")
        self._event(
            agent, EventType.GIT_COMMIT, task=task, payload={"commit": sha, "branch": state.branch}
        )
        try:
            git.push(state.branch)
        except Exception as exc:  # noqa: BLE001
            log.warning("push failed for %s: %s", state.branch, exc)
        return [sha]

    def _set_agent(
        self,
        agent_id: str,
        status: AgentStatus,
        *,
        current_task_id: str | None = None,
        error: str | None = None,
        clear_task: bool = False,
    ) -> None:
        with session_scope() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return
            agent.status = status
            if clear_task:
                agent.current_task_id = None
            elif current_task_id is not None:
                agent.current_task_id = current_task_id
            if error is not None:
                agent.error = error
            session.add(
                Event(
                    type=str(EventType.AGENT_STATUS),
                    project_id=agent.project_id,
                    agent_id=agent.id,
                    payload={"status": str(status)},
                )
            )

    def _append_message(self, agent_id: str, role: str, content: str) -> None:
        with session_scope() as session:
            agent = session.get(Agent, agent_id)
            if agent is None:
                return
            conversation = list(agent.conversation or [])
            conversation.append({"role": role, "content": content})
            agent.conversation = conversation

    def _event(
        self,
        agent: Agent,
        type_: EventType,
        *,
        task: Task | None = None,
        payload: dict | None = None,
    ) -> None:
        with session_scope() as session:
            session.add(
                Event(
                    type=str(type_),
                    project_id=agent.project_id,
                    agent_id=agent.id,
                    task_id=task.id if task else None,
                    payload=payload or {},
                )
            )


__all__ = ["AgentManager", "Project"]
