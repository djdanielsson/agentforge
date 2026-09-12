"""Git endpoints — proxied through the workspace controller into the k3s pod.

Every command runs inside the project's workspace at /workspace. The API never
touches a host filesystem.
"""

from __future__ import annotations

from agentforge_shared.enums import WorkspaceStatus
from agentforge_shared.models import Project
from agentforge_shared.schemas import GitCommitRequest, GitDiff, GitFileChange, GitMergeRequest
from fastapi import APIRouter, HTTPException, status

from ..deps import DbSession

router = APIRouter(prefix="/projects/{project_id}/git", tags=["git"])


def _workspace(session, project_id: str):
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    workspace = project.workspace
    if workspace is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "workspace not provisioned")
    if workspace.status != WorkspaceStatus.READY:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"workspace is {workspace.status}; git is unavailable until it is ready",
        )
    return project, workspace


def _exec(workspace, command: list[str]) -> str:
    """Run a command inside the workspace pod. Imported lazily so the API can
    start without a kubeconfig present."""
    from agentforge_workspaces.controller import exec_in_workspace

    return exec_in_workspace(workspace.namespace, workspace.pod_name, command)


@router.get("/diff", response_model=GitDiff)
def get_diff(session: DbSession, project_id: str, base: str | None = None) -> GitDiff:
    project, workspace = _workspace(session, project_id)
    base_ref = base or project.default_branch
    diff = _exec(workspace, ["git", "diff", f"{base_ref}...HEAD"])
    names = _exec(workspace, ["git", "diff", "--numstat", f"{base_ref}...HEAD"])
    files = []
    for line in names.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            added, removed, path = parts
            files.append(
                GitFileChange(
                    path=path,
                    status="modified",
                    additions=int(added) if added.isdigit() else 0,
                    deletions=int(removed) if removed.isdigit() else 0,
                )
            )
    branch = _exec(workspace, ["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    return GitDiff(branch=branch, base=base_ref, diff=diff, files=files)


@router.get("/status")
def get_status(session: DbSession, project_id: str) -> dict:
    _project, workspace = _workspace(session, project_id)
    return {
        "status": _exec(workspace, ["git", "status", "--porcelain"]),
        "branch": _exec(workspace, ["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip(),
        "log": _exec(workspace, ["git", "log", "--oneline", "-20"]),
    }


@router.post("/commit")
def commit(session: DbSession, project_id: str, payload: GitCommitRequest) -> dict:
    _project, workspace = _workspace(session, project_id)
    paths = payload.paths + ["-A"] if payload.paths else []
    _exec(workspace, ["git", "add", *paths] if paths else ["git", "add", "-A"])
    out = _exec(workspace, ["git", "commit", "-m", payload.message])
    sha = _exec(workspace, ["git", "rev-parse", "HEAD"]).strip()
    return {"commit": sha, "output": out}


@router.post("/merge")
def merge(session: DbSession, project_id: str, payload: GitMergeRequest) -> dict:
    project, workspace = _workspace(session, project_id)
    target = payload.target_branch or project.default_branch
    out = _exec(workspace, ["git", "checkout", target])
    out += _exec(
        workspace,
        [
            "git",
            "merge",
            "--no-ff",
            "-m",
            f"Merge {payload.source_branch} into {target}",
            payload.source_branch,
        ],
    )
    return {"target": target, "output": out}
