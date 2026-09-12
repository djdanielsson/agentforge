"""Agent lifecycle and task dispatch.

Takes a queued task, hands it to the workspace's OpenHands Agent Server, turns
whatever comes back into AgentForge events, and commits the result. All
knowledge of the Agent Server's API lives in `agentforge-agent-server`, so this
module deals only in our own vocabulary.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from agentforge_agent_server import (
    AgentEvent,
    AgentServerClient,
    AgentServerError,
    AgentSpec,
)
from agentforge_shared.config import get_settings
from agentforge_shared.db import session_scope
from agentforge_shared.enums import AgentStatus, EventType, TaskKind, TaskStatus, WorkspaceStatus
from agentforge_shared.events import record_event
from agentforge_shared.models import Agent, Task, Workspace
from sqlalchemy import select

log = logging.getLogger(__name__)

ClientFactory = Callable[[str], AgentServerClient]


def _default_client_factory(url: str) -> AgentServerClient:
    settings = get_settings()
    return AgentServerClient(url, api_key=settings.agent_server_api_key)


class AgentManager:
    def __init__(self, client_factory: ClientFactory | None = None) -> None:
        self.settings = get_settings()
        self._client_factory = client_factory or _default_client_factory

    # --- selection --------------------------------------------------------

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
            workspace = session.scalars(
                select(Workspace).where(Workspace.project_id == agent.project_id)
            ).first()
            if workspace is not None:
                session.expunge(workspace)
            return workspace

    def _client(self, workspace: Workspace) -> AgentServerClient:
        url = workspace.agent_server_url or self.settings.openhands_url
        return self._client_factory(url)

    # --- dispatch ---------------------------------------------------------

    def dispatch(self, agent: Agent, task: Task) -> None:
        """Run one task to completion (or to a stop that needs a human)."""
        workspace = self.workspace_for(agent)
        if workspace is None or workspace.status != WorkspaceStatus.READY:
            log.info("agent %s has no ready workspace; leaving task %s queued", agent.name, task.id)
            return

        self._set_agent(agent.id, AgentStatus.WORKING, current_task_id=task.id)
        self._event(
            agent,
            EventType.AGENT_STARTED,
            task=task,
            payload={"agent": agent.name, "model": agent.model, "kind": task.kind},
        )
        self._event(
            agent,
            EventType.AGENT_PROGRESS,
            task=task,
            payload={"message": f"picked up: {task.description[:120]}", "kind": task.kind},
        )
        self._event(
            agent, EventType.TASK_STATUS, task=task, payload={"status": str(TaskStatus.RUNNING)}
        )

        try:
            with self._client(workspace) as client:
                session_id = self._ensure_session(client, agent, workspace)
                event = client.send_message(session_id, self._prompt_for(task, agent))
        except AgentServerError as exc:
            log.error("agent %s failed task %s: %s", agent.name, task.id, exc)
            self._fail(agent, task, str(exc))
            return

        self._record_reply(agent, task, event)

    def _prompt_for(self, task: Task, agent: Agent) -> str:
        """Frame a bare instruction as the kind of work it is.

        The Agent Server does not need to know about our task taxonomy, but it
        does need to be told what outcome is expected.
        """
        if task.kind == TaskKind.REVIEW:
            return f"Review the current changes and report findings. {task.description}"
        if task.kind == TaskKind.TEST:
            return f"Run the test suite and report the result. {task.description}"
        if task.kind == TaskKind.DEPLOY:
            return f"Prepare the deployment described and report what you did. {task.description}"
        return task.description

    def _record_reply(self, agent: Agent, task: Task, event: AgentEvent) -> None:
        """Translate one agent turn into events, task state and commits."""
        if event.error:
            self._event(agent, EventType.AGENT_FAILED, task=task, payload={"error": event.error})
            self._fail(agent, task, event.error)
            return

        if event.permission_request:
            self._set_agent(agent.id, AgentStatus.AWAITING_APPROVAL)
            self._event(
                agent,
                EventType.AGENT_PERMISSION_REQUIRED,
                task=task,
                payload=event.permission_request,
            )
            self._block_task(task.id, event.permission_request.get("reason") or "awaiting approval")
            return

        if event.blocked:
            question = event.question or event.content or "needs input"
            self._set_agent(agent.id, AgentStatus.BLOCKED)
            self._event(agent, EventType.AGENT_WAITING, task=task, payload={"question": question})
            self._block_task(task.id, question)
            return

        if event.content:
            self._append_message(agent.id, "assistant", event.content)
            self._event(
                agent,
                EventType.AGENT_MESSAGE,
                task=task,
                payload={"role": "assistant", "content": event.content},
            )

        commits = self._commit_if_dirty(agent, task)
        with session_scope() as session:
            row = session.get(Task, task.id)
            if row is not None:
                row.status = TaskStatus.SUCCEEDED
                row.result = event.content
                row.commits = commits

        self._event(
            agent,
            EventType.AGENT_COMPLETED,
            task=task,
            payload={
                "task_id": task.id,
                "kind": task.kind,
                "result": event.content,
                "commits": commits,
            },
        )
        self._emit_kind_outcome(agent, task, commits)
        self._set_agent(agent.id, AgentStatus.IDLE, clear_task=True)

    def _emit_kind_outcome(self, agent: Agent, task: Task, commits: list[str]) -> None:
        """Emit the outcome event for a semantic task.

        A subscriber that cares about test results listens for `test.completed`
        rather than filtering agent chatter.
        """
        payload = {"task_id": task.id, "commits": commits, **(task.payload or {})}
        mapping = {
            TaskKind.TEST: EventType.TEST_COMPLETED,
            TaskKind.REVIEW: EventType.REVIEW_COMPLETED,
            TaskKind.DEPLOY: EventType.DEPLOY_COMPLETED,
        }
        try:
            event_type = mapping.get(TaskKind(str(task.kind)))
        except ValueError:
            event_type = None
        if event_type is not None:
            self._event(agent, event_type, task=task, payload=payload)

    # --- session ----------------------------------------------------------

    def _ensure_session(self, client: AgentServerClient, agent: Agent, workspace: Workspace) -> str:
        """Create the Agent Server agent and conversation once, then reuse them."""
        if agent.session_id:
            return agent.session_id

        server_agent = client.create_agent(
            AgentSpec(
                name=agent.name,
                model=agent.model,
                workspace_id=workspace.namespace,
                metadata={
                    "agentforge_agent_id": agent.id,
                    "agentforge_project_id": agent.project_id,
                    "agentforge_branch": agent.branch,
                },
            )
        )
        server_agent_id = str(server_agent.get("id") or server_agent.get("agent_id") or agent.id)

        run = client.start_conversation(server_agent_id)
        with session_scope() as session:
            session.get(Agent, agent.id).session_id = run.session_id
        self._event(agent, EventType.AGENT_STARTED, payload={"session_id": run.session_id})
        return run.session_id

    # --- git --------------------------------------------------------------

    def _commit_if_dirty(self, agent: Agent, task: Task) -> list[str]:
        workspace = self.workspace_for(agent)
        if workspace is None or not workspace.pod_name:
            return []

        from .git_manager import GitManager

        git = GitManager(workspace.namespace, workspace.pod_name)
        try:
            state = git.state()
        except Exception as exc:  # noqa: BLE001 - the pod may be mid-restart
            log.warning("git state unavailable for %s: %s", workspace.namespace, exc)
            return []
        if state.clean:
            return []

        git.ensure_branch(agent.branch or f"agent/{agent.id}")
        sha = git.commit_all(f"{agent.name}: {task.description[:60]}")
        self._event(
            agent,
            EventType.COMMIT_CREATED,
            task=task,
            payload={"commit": sha, "branch": state.branch},
        )

        # Pushing is opt-in: it is the point where agent output leaves the sandbox.
        from agentforge_shared.permissions import AgentPermissions

        if AgentPermissions.from_dict(agent.policy).git.push:
            try:
                git.push(state.branch)
            except Exception as exc:  # noqa: BLE001
                log.warning("push failed for %s: %s", state.branch, exc)
        return [sha]

    # --- state helpers ----------------------------------------------------

    def _fail(self, agent: Agent, task: Task, error: str) -> None:
        with session_scope() as session:
            row = session.get(Task, task.id)
            if row is not None:
                row.status = TaskStatus.FAILED
                row.error = error[:2000]
        self._set_agent(agent.id, AgentStatus.ERROR, error=error[:2000])

    def _block_task(self, task_id: str, reason: str) -> None:
        with session_scope() as session:
            row = session.get(Task, task_id)
            if row is not None:
                row.status = TaskStatus.BLOCKED
                row.result = reason

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
            record_event(
                session,
                type=EventType.AGENT_STATUS,
                project_id=agent.project_id,
                agent_id=agent.id,
                payload={"status": str(status)},
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
            record_event(
                session,
                type=type_,
                project_id=agent.project_id,
                agent_id=agent.id,
                task_id=task.id if task else None,
                payload=payload or {},
            )
