"""Reconciles Workspace rows against real Kubernetes objects."""

from __future__ import annotations

import logging

from aiw_shared.db import session_scope
from aiw_shared.enums import EventType, ProjectStatus, WorkspaceStatus
from aiw_shared.models import Event, Project, Workspace
from aiw_workspace.controller import WorkspaceController
from aiw_workspace.k8s import KubernetesUnavailable
from sqlalchemy import select

log = logging.getLogger(__name__)


class WorkspaceManager:
    def __init__(self, controller: WorkspaceController | None = None) -> None:
        self.controller = controller or WorkspaceController()

    def pending(self) -> list[Workspace]:
        with session_scope() as session:
            rows = session.scalars(
                select(Workspace).where(
                    Workspace.status.in_([WorkspaceStatus.PENDING, WorkspaceStatus.PROVISIONING])
                )
            )
            for row in rows:
                session.expunge(row)
            return list(rows)

    def provisioning(self) -> list[Workspace]:
        with session_scope() as session:
            rows = session.scalars(
                select(Workspace).where(Workspace.status == WorkspaceStatus.PROVISIONING)
            )
            return list(rows)

    def reconcile_one(self, workspace: Workspace) -> None:
        with session_scope() as session:
            project = session.get(Project, workspace.project_id)
            if project is None:
                return
            repo, revision, ns = project.repository_url, project.default_branch, workspace.namespace
            session.get(Workspace, workspace.id).status = WorkspaceStatus.PROVISIONING
            session.add(
                Event(
                    type=str(EventType.WORKSPACE_STATUS),
                    project_id=project.id,
                    payload={"status": str(WorkspaceStatus.PROVISIONING), "namespace": ns},
                )
            )

        try:
            info = self.controller.provision(
                namespace=ns, repository_url=repo, revision=revision, wait=False
            )
        except KubernetesUnavailable as exc:
            self._fail(workspace.id, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - surface anything k8s throws
            log.exception("provision failed for %s", ns)
            self._fail(workspace.id, str(exc))
            return

        with session_scope() as session:
            row = session.get(Workspace, workspace.id)
            row.namespace = info.namespace
            row.pvc_name = info.pvc_name
            row.pod_name = info.pod_name
            row.service_name = info.service_name
            row.code_server_url = info.code_server_url
            row.image = self.controller.settings.workspace_image
            session.add(
                Event(
                    type=str(EventType.WORKSPACE_STATUS),
                    project_id=row.project_id,
                    payload={"status": str(WorkspaceStatus.CLONING), "namespace": ns},
                )
            )

    def reconcile_readiness(self) -> None:
        """Move CLONING workspaces to READY once the pod is up."""
        with session_scope() as session:
            rows = session.scalars(
                select(Workspace).where(Workspace.status == WorkspaceStatus.CLONING)
            ).all()
            for row in rows:
                try:
                    state = self.controller.status(row.namespace, row.pod_name or "")
                except Exception:  # noqa: BLE001
                    continue
                if not state.get("exists"):
                    continue
                ready = any(
                    c["type"] == "Ready" and c["status"] == "True"
                    for c in state.get("conditions", [])
                )
                if ready:
                    row.status = WorkspaceStatus.READY
                    project = session.get(Project, row.project_id)
                    if project is not None:
                        project.status = ProjectStatus.READY
                    session.add(
                        Event(
                            type=str(EventType.WORKSPACE_STATUS),
                            project_id=row.project_id,
                            payload={
                                "status": str(WorkspaceStatus.READY),
                                "code_server_url": row.code_server_url,
                            },
                        )
                    )
                    log.info("workspace %s is READY", row.namespace)
                elif state.get("phase") in ("Failed",):
                    row.status = WorkspaceStatus.ERROR
                    row.error = f"pod entered phase {state.get('phase')}"

    def _fail(self, workspace_id: str, error: str) -> None:
        with session_scope() as session:
            row = session.get(Workspace, workspace_id)
            if row is None:
                return
            row.status = WorkspaceStatus.ERROR
            row.error = error[:2000]
            session.add(
                Event(
                    type=str(EventType.WORKSPACE_STATUS),
                    project_id=row.project_id,
                    payload={"status": str(WorkspaceStatus.ERROR), "error": error[:500]},
                )
            )
