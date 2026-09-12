"""Reconciles Workspace rows against whatever backend the provider talks to.

This module knows about projects, secrets and policies. It knows nothing about
Kubernetes or podman — that is the provider's job. Keeping that line sharp is
what makes a second backend cheap.
"""

from __future__ import annotations

import logging

from agentforge_shared.config import get_settings
from agentforge_shared.db import session_scope
from agentforge_shared.enums import EventType, ProjectStatus, WorkspaceStatus
from agentforge_shared.events import record_event
from agentforge_shared.models import Agent, Project, Workspace
from agentforge_shared.secrets import effective_permissions, resolve_secrets
from agentforge_workspaces.providers import (
    ProviderError,
    WorkspaceProvider,
    WorkspaceSpec,
    get_provider,
)
from sqlalchemy import select

log = logging.getLogger(__name__)


class WorkspaceManager:
    def __init__(self, provider: WorkspaceProvider | None = None) -> None:
        self.settings = get_settings()
        self.provider = provider or get_provider(self.settings.workspace_provider)

    # --- selection --------------------------------------------------------

    def _queue(self, statuses: list[str]) -> list[Workspace]:
        with session_scope() as session:
            rows = session.scalars(select(Workspace).where(Workspace.status.in_(statuses))).all()
            for row in rows:
                session.expunge(row)
            return list(rows)

    def pending(self) -> list[Workspace]:
        return self._queue([WorkspaceStatus.PENDING])

    def cloning(self) -> list[Workspace]:
        return self._queue([WorkspaceStatus.CLONING, WorkspaceStatus.PROVISIONING])

    def deletable(self) -> list[Workspace]:
        """Workspaces whose project has been archived or whose deletion was requested."""
        return self._queue([WorkspaceStatus.DELETING])

    # --- reconcile --------------------------------------------------------

    def _build_spec(self, session, workspace: Workspace) -> WorkspaceSpec:
        project = session.get(Project, workspace.project_id)
        if project is None:
            raise ProviderError(f"workspace {workspace.id} has no project")

        # A workspace serves the project; the first agent's policy is the one
        # that shapes it. Multi-agent projects get one shared workspace in MVP
        # #1, so the policy must be the intersection of what the agents need.
        agent = session.scalars(
            select(Agent).where(Agent.project_id == project.id).order_by(Agent.created_at)
        ).first()
        permissions = effective_permissions(agent)

        secrets = []
        if permissions.secrets.enabled:
            secrets = resolve_secrets(
                session,
                project_id=project.id,
                agent_id=agent.id if agent else None,
                provider=self.provider.name,
            )

        return WorkspaceSpec(
            project_id=project.id,
            slug=project.slug,
            name=project.name,
            reference=workspace.namespace,
            repository_url=project.repository_url,
            revision=project.default_branch,
            image=self.settings.workspace_image,
            agent_image=self.settings.workspace_agent_image,
            cpu_request=self.settings.workspace_cpu_request,
            memory_request=self.settings.workspace_memory_request,
            storage=self.settings.workspace_storage,
            storage_class=self.settings.workspace_storage_class,
            code_server_port=self.settings.workspace_code_server_port,
            permissions=permissions,
            secrets=secrets,
            environment={
                "AGENTFORGE_GATEWAY_URL": self.settings.llm_gateway_url,
                "AGENTFORGE_AGENT_SERVER_URL": f"http://{workspace.namespace}-ws:3000",
                "OPENHANDS_LLM_MODEL": agent.model if agent else self.settings.default_agent_model,
            },
            labels={"agentforge.io/project": project.slug},
        )

    def reconcile_one(self, workspace: Workspace) -> None:
        workspace_id = workspace.id
        with session_scope() as session:
            row = session.get(Workspace, workspace_id)
            spec = self._build_spec(session, row)
            row.status = WorkspaceStatus.PROVISIONING
            row.provider = self.provider.name
            record_event(
                session,
                type=EventType.WORKSPACE_CREATED,
                project_id=row.project_id,
                payload={"namespace": row.namespace, "provider": self.provider.name},
            )

        try:
            state = self.provider.create(spec)
            self.provider.inject_secrets(spec.reference, spec.secrets)
        except (ProviderError, ValueError) as exc:
            log.error("provisioning failed for %s: %s", spec.reference, exc)
            self._fail(workspace_id, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - a backend can fail in new ways
            log.exception("unexpected provisioning failure for %s", spec.reference)
            self._fail(workspace_id, str(exc))
            return

        with session_scope() as session:
            row = session.get(Workspace, workspace_id)
            row.status = WorkspaceStatus.CLONING
            row.provider = self.provider.name
            row.code_server_url = state.code_server_url
            row.agent_server_url = state.agent_server_url
            # Without these the git manager has no pod to exec into, so every
            # commit silently no-ops.
            row.pvc_name = state.pvc_name
            row.pod_name = state.pod_name
            row.service_name = state.service_name
            row.image = spec.image
            if state.detail.get("warnings"):
                row.error = "; ".join(state.detail["warnings"])[:2000]
            record_event(
                session,
                type=EventType.AGENT_PROGRESS,
                project_id=row.project_id,
                payload={"status": str(WorkspaceStatus.CLONING), "namespace": row.namespace},
            )

    def reconcile_readiness(self) -> None:
        """Observe reality and move workspaces to READY or ERROR."""
        for workspace in self.cloning():
            try:
                state = self.provider.get_status(workspace.namespace)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not read state for %s: %s", workspace.namespace, exc)
                continue

            status = WorkspaceStatus(state.status)
            if status == WorkspaceStatus.READY:
                self._mark_ready(workspace.id, state)
            elif status == WorkspaceStatus.ERROR:
                self._fail(workspace.id, "workspace entered an error state", from_state=workspace)

    def _mark_ready(self, workspace_id: str, state) -> None:
        with session_scope() as session:
            row = session.get(Workspace, workspace_id)
            if row is None or row.status == WorkspaceStatus.READY:
                return
            row.status = WorkspaceStatus.READY
            row.code_server_url = state.code_server_url or row.code_server_url
            row.agent_server_url = state.agent_server_url or row.agent_server_url
            project = session.get(Project, row.project_id)
            if project is not None:
                project.status = ProjectStatus.READY
            record_event(
                session,
                type=EventType.WORKSPACE_READY,
                project_id=row.project_id,
                payload={
                    "namespace": row.namespace,
                    "code_server_url": row.code_server_url,
                    "agent_server_url": row.agent_server_url,
                },
            )
            log.info("workspace %s is READY", row.namespace)

    def reconcile_deletions(self) -> None:
        for workspace in self.deletable():
            try:
                self.provider.destroy(workspace.namespace)
            except Exception as exc:  # noqa: BLE001
                log.warning("teardown failed for %s: %s", workspace.namespace, exc)
                continue
            with session_scope() as session:
                row = session.get(Workspace, workspace.id)
                if row is None:
                    continue
                row.status = WorkspaceStatus.DESTROYED
                record_event(
                    session,
                    type=EventType.WORKSPACE_DESTROYED,
                    project_id=row.project_id,
                    payload={"namespace": row.namespace, "provider": self.provider.name},
                )

    def _fail(self, workspace_id: str, error: str, *, from_state: Workspace | None = None) -> None:
        with session_scope() as session:
            row = session.get(Workspace, workspace_id)
            if row is None:
                return
            row.status = WorkspaceStatus.ERROR
            row.error = error[:2000]
            record_event(
                session,
                type=EventType.WORKSPACE_FAILED,
                project_id=row.project_id,
                payload={"error": error[:500], "namespace": row.namespace},
            )
