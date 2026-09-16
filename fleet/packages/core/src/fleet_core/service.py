"""Orchestration: the control plane's own logic (SPEC §40, §34).

Routers stay thin and providers stay ignorant of each other. This module is
where "a project needs a namespace, a workspace row, a token and an agent that
points at it" lives.

Desired-state note (SPEC §34): creating a project records the workspace it
*wants* and returns immediately; provisioning is reconciled in the background and
reported through the workspace's status and the event stream.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

from sqlalchemy import select

from .agents import describe_agent_providers, get_agent_provider
from .config import Settings, get_settings
from .db import session_scope
from .events import publish_sync
from .llm import project_token
from .models import Agent, Credential, Project, Task, Workspace
from .secrets import copy_secret, secret_keys, secret_present, upsert_secret
from .workspaces import SecretRef, WorkspaceSpec, get_workspace_provider
from .workspaces.definition import render_definition


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    pass


log = logging.getLogger(__name__)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "project"


def _describe(exc: Exception) -> str:
    """A short, useful error string.

    A Kubernetes ApiException renders its whole HTTP response, headers included,
    which buried the actual reason in the event log and the workspace row.
    """
    from kubernetes.client.exceptions import ApiException

    if isinstance(exc, ApiException):
        return f"{exc.status} {exc.reason}: {(exc.body or '')[:300]}"
    return f"{type(exc).__name__}: {exc}"


def _reference(settings: Settings, slug: str, provider: str = "") -> str:
    """The provider's handle for a project's workspace.

    For the Kubernetes providers this is the namespace (the isolation boundary,
    SPEC §18), so it is derived from the project name and is stable for the
    project's lifetime. For a checkout workspace it is the directory name on the
    shared volume — the same slug, with no namespace prefix, because there is no
    namespace to name.
    """
    if provider == "checkout":
        return slug[:63].rstrip("-")
    return f"{settings.namespace_prefix}{slug}"[:63].rstrip("-")


# --- reads -------------------------------------------------------------------


def get_project(session, name_or_id: str) -> Project:
    project = session.execute(
        select(Project).where((Project.id == name_or_id) | (Project.name == name_or_id))
    ).scalar_one_or_none()
    if project is None:
        raise NotFound(f"no project {name_or_id!r}")
    return project


def get_agent(session, project_id: str, name_or_id: str) -> Agent:
    agent = session.execute(
        select(Agent).where(
            Agent.project_id == project_id,
            (Agent.id == name_or_id) | (Agent.name == name_or_id),
        )
    ).scalar_one_or_none()
    if agent is None:
        raise NotFound(f"no agent {name_or_id!r} in project {project_id}")
    return agent


def primary_workspace(session, project_id: str) -> Workspace:
    workspace = (
        session.execute(
            select(Workspace)
            .where(Workspace.project_id == project_id)
            .order_by(Workspace.created_at)
        )
        .scalars()
        .first()
    )
    if workspace is None:
        raise NotFound(f"project {project_id} has no workspace")
    return workspace


# --- projects ----------------------------------------------------------------


def create_project(payload: dict[str, Any], *, provision: bool = True) -> dict[str, Any]:
    settings = get_settings()
    name = str(payload.get("name") or "").strip()
    if not name:
        raise Conflict("a project needs a name")
    slug = slugify(name)

    with session_scope() as session:
        existing = session.execute(select(Project).where(Project.name == name)).scalar_one_or_none()
        if existing is not None:
            raise Conflict(f"project {name!r} already exists")

        workspace_cfg = payload.get("workspace") or {}
        repository = payload.get("repository") or {}
        # The provider is chosen per project: a checkout project lands in the
        # shared T3 environment, a devpod project gets its own namespace. The
        # reference has to follow the provider, so it is resolved from the same
        # value rather than from the deployment default.
        provider_name = workspace_cfg.get("provider") or settings.workspace_provider
        project = Project(
            name=name,
            description=payload.get("description", ""),
            repository_url=repository.get("url", ""),
            repository_provider=repository.get("provider", "github"),
            repository_branch=repository.get("branch", "main"),
            workspace_provider=provider_name,
            cpu=workspace_cfg.get("cpu", settings.workspace_cpu),
            memory=workspace_cfg.get("memory", settings.workspace_memory),
            storage=workspace_cfg.get("storage", settings.workspace_storage),
            llm_policy=payload.get("llmPolicy") or {"mode": "localOnly"},
            policies=payload.get("policies") or {},
        )
        session.add(project)
        session.flush()

        workspace = Workspace(
            project_id=project.id,
            name=payload.get("workspaceName", "default"),
            provider=project.workspace_provider,
            reference=_reference(settings, slug, provider_name),
            status="pending",
        )
        session.add(workspace)

        credentials = payload.get("credentials") or []
        for credential in credentials:
            session.add(
                Credential(
                    project_id=project.id,
                    name=credential["name"],
                    secret_name=f"{slug}-credentials",
                    key=credential.get("key", credential["name"]),
                    env_var=credential.get("envVar") or f"{credential['name'].upper()}_TOKEN",
                    description=credential.get("description", ""),
                    allowed_agents=credential.get("allowedAgents") or [],
                )
            )
        session.flush()
        # Re-read detached copies so the caller sees ids after commit.
        result = _project_detail(session, project)

    for credential in payload.get("credentials") or []:
        if credential.get("value"):
            store_credential_value(slug, credential["name"], credential["value"])

    publish_sync(
        "project.created",
        message=f"project {name} created",
        project_id=result["id"],
        payload={"workspace": result["workspaces"][0]["reference"]},
    )

    if provision:
        threading.Thread(
            target=provision_workspace, args=(result["id"],), daemon=True, name=f"prov-{slug}"
        ).start()
    return result


def _project_detail(session, project: Project) -> dict[str, Any]:
    workspaces = (
        session.execute(select(Workspace).where(Workspace.project_id == project.id)).scalars().all()
    )
    agents = session.execute(select(Agent).where(Agent.project_id == project.id)).scalars().all()
    credentials = (
        session.execute(select(Credential).where(Credential.project_id == project.id))
        .scalars()
        .all()
    )
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "repository": {
            "url": project.repository_url,
            "provider": project.repository_provider,
            "branch": project.repository_branch,
        },
        "workspace": {
            "provider": project.workspace_provider,
            "cpu": project.cpu,
            "memory": project.memory,
            "storage": project.storage,
        },
        "llmPolicy": project.llm_policy,
        "policies": project.policies,
        "createdAt": project.created_at.isoformat() if project.created_at else None,
        "workspaces": [_workspace_detail(w) for w in workspaces],
        "agents": [_agent_detail(a) for a in agents],
        # Metadata only. Never a value (SPEC §16).
        "credentials": [
            {
                "id": c.id,
                "name": c.name,
                "secretRef": {"name": c.secret_name, "key": c.key},
                "envVar": c.env_var,
                "allowedAgents": c.allowed_agents,
            }
            for c in credentials
        ],
    }


def _workspace_detail(workspace: Workspace) -> dict[str, Any]:
    return {
        "id": workspace.id,
        "name": workspace.name,
        "provider": workspace.provider,
        "reference": workspace.reference,
        "cluster": workspace.reference,  # the namespace is the cluster boundary
        "status": workspace.status,
        "error": workspace.error,
        "pod": workspace.pod_name,
        "pvc": workspace.pvc_name,
        "service": workspace.service_name,
        "url": workspace.detail.get("url", "") if workspace.detail else "",
        "t3_url": workspace.detail.get("t3_url", "") if workspace.detail else "",
        "detail": workspace.detail or {},
        "updatedAt": workspace.updated_at.isoformat() if workspace.updated_at else None,
    }


def _agent_detail(agent: Agent) -> dict[str, Any]:
    return {
        "id": agent.id,
        "name": agent.name,
        "provider": agent.provider,
        "role": agent.role,
        "model": agent.model,
        "status": agent.status,
        "currentTask": agent.current_task_id,
        "worktree": agent.worktree,
        "workspace": (agent.config or {}).get("workspace_reference", ""),
        "endpoint": (agent.config or {}).get("endpoint", ""),
        "config": agent.config or {},
    }


def list_projects() -> list[dict[str, Any]]:
    with session_scope() as session:
        projects = session.execute(select(Project).order_by(Project.created_at)).scalars().all()
        return [_project_detail(session, p) for p in projects]


def get_project_detail(name_or_id: str) -> dict[str, Any]:
    with session_scope() as session:
        return _project_detail(session, get_project(session, name_or_id))


def delete_project(name_or_id: str) -> None:
    """Forget a project and tear its workspace down.

    The row disappears immediately and the destruction happens on a worker
    thread: destroying a DevPod workspace is a CLI call that can take minutes,
    and a DELETE that blocks that long reads as a hang. The route already
    answers 202; this makes that answer truthful.
    """
    with session_scope() as session:
        project = get_project(session, name_or_id)
        workspace = (
            session.execute(select(Workspace).where(Workspace.project_id == project.id))
            .scalars()
            .first()
        )
        reference = workspace.reference if workspace else ""
        # The *project's* provider, not the deployment's default: with two
        # providers in play, tearing a checkout down through the DevPod provider
        # (or the other way round) would target the wrong thing entirely.
        provider_name = (
            workspace.provider if workspace and workspace.provider else project.workspace_provider
        )
        project_id, project_name = project.id, project.name
        for model in (Task, Agent, Workspace, Credential):
            session.query(model).filter(model.project_id == project.id).delete()
        session.delete(project)

    publish_sync(
        "project.deleted", message=f"project {project_name} deleted", project_id=project_id
    )
    if reference:
        threading.Thread(
            target=_destroy_workspace,
            args=(reference, project_id, provider_name),
            daemon=True,
            name=f"destroy-{reference}",
        ).start()


def _destroy_workspace(reference: str, project_id: str, provider_name: str = "") -> None:
    provider = get_workspace_provider(provider_name or None, get_settings())
    try:
        provider.destroy(reference)
        publish_sync(
            "workspace.destroyed",
            message=f"workspace {reference} destroyed",
            project_id=project_id,
            payload={"reference": reference},
        )
    except Exception as exc:  # noqa: BLE001 - the row is gone; report loudly
        log.error("failed to destroy workspace %s: %s", reference, exc)
        publish_sync(
            "workspace.destroy_failed",
            message=f"{type(exc).__name__}: {exc}",
            project_id=project_id,
            payload={"reference": reference},
        )


# --- workspaces --------------------------------------------------------------


def _spec_for(session, project: Project, workspace: Workspace, settings: Settings) -> WorkspaceSpec:
    """Build the provider spec for a project's workspace."""
    credentials = (
        session.execute(select(Credential).where(Credential.project_id == project.id))
        .scalars()
        .all()
    )
    slug = slugify(project.name)

    secret_refs: list[SecretRef] = []
    environment: dict[str, str] = {
        # The agent's model access: a token for the fleet gateway, scoped to
        # this project — never a model-provider key (SPEC §11, §13).
        "FLEET_LLM_TOKEN": project_token(project.id),
        "FLEET_LLM_BASE_URL": f"{(settings.control_plane_url or f'http://{settings.namespace}-api.{settings.namespace}.svc.cluster.local:8000').rstrip('/')}/llm/v1",
        "FLEET_LLM_MODEL": project.llm_policy.get("model", settings.llm_default_model)
        if project.llm_policy
        else settings.llm_default_model,
        # Which logical aliases the workspace's opencode config declares; the
        # gateway decides what they resolve to (SPEC §12).
        "FLEET_LLM_MODELS": ",".join(settings.llm_models),
        "FLEET_GATEWAY": settings.llm_gateway_url,
    }
    for credential in credentials:
        secret_refs.append(
            SecretRef(
                name=credential.name,
                secret_name=credential.secret_name,
                key=credential.key,
                env_var=credential.env_var,
                required=False,
            )
        )

    return WorkspaceSpec(
        project_id=project.id,
        project_name=project.name,
        reference=workspace.reference,
        image=settings.workspace_image,
        cpu=project.cpu,
        memory=project.memory,
        storage=project.storage,
        repository_url=project.repository_url,
        repository_branch=project.repository_branch,
        environment=environment,
        secrets=secret_refs,
        t3_enabled=settings.t3_enabled,
        definition_dir=str(settings.data_dir / "projects" / slug / "workspace"),
    )


def provision_workspace(project_id: str, *, force: bool = False) -> dict[str, Any]:
    """Create (or re-create) a project's workspace through the provider."""
    settings = get_settings()
    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise NotFound(f"no project {project_id}")
        workspace = primary_workspace(session, project.id)
        spec = _spec_for(session, project, workspace, settings)
        slug = slugify(project.name)
        workspace.status = "provisioning"
        workspace.error = ""
        session.flush()
        workspace_id = workspace.id
        reference = workspace.reference
        project_name = project.name
        # The project's own provider, not the deployment default: a checkout
        # project logged "via devpod", which is wrong and misleading.
        provider_name = project.workspace_provider or settings.workspace_provider

    publish_sync(
        "workspace.creating",
        message=f"provisioning workspace {reference} via {provider_name}",
        project_id=project_id,
        payload={"reference": reference},
    )

    try:
        # Project credentials live in the control-plane namespace and are copied
        # into the workspace's own namespace, so the pod resolves them with
        # secretKeyRef and nothing else in the platform holds the value.
        #
        # Order matters: the namespace has to exist first, and the secret has to
        # be there before the pod starts. `prepare` creates the boundary,
        # `create` starts the pod.
        provider = get_workspace_provider(project.workspace_provider or None, settings)
        definition = render_definition(
            spec, settings, settings.data_dir / "projects" / slug / "workspace"
        )
        spec.definition_dir = str(definition)
        provider.prepare(spec)
        # Copying the project's Secret into the workspace is a Kubernetes-provider
        # idea: it needs a namespace to copy *into*. A checkout workspace has
        # none, and asks the control plane's own namespace for the value when it
        # writes the file (SPEC §16).
        if provider.credentials_in_namespace:
            _project_credentials_into_namespace(settings, project_id, reference)
        state = provider.create(spec)
        _store_state(workspace_id, state, provider.name, project_name, project_id, settings)
        return _workspace_by_id(workspace_id)
    except Exception as exc:  # noqa: BLE001 - failure is a state, not a crash
        log.exception("workspace provisioning failed for %s", reference)
        with session_scope() as session:
            workspace = session.get(Workspace, workspace_id)
            if workspace is not None:
                workspace.status = "failed"
                workspace.error = _describe(exc)
        publish_sync(
            "workspace.failed",
            message=_describe(exc),
            project_id=project_id,
            payload={"reference": reference},
        )
        return _workspace_by_id(workspace_id)


def _project_credentials_into_namespace(
    settings: Settings, project_id: str, reference: str
) -> None:
    with session_scope() as session:
        credentials = (
            session.execute(select(Credential).where(Credential.project_id == project_id))
            .scalars()
            .all()
        )
        names = {c.secret_name for c in credentials}
    for name in names:
        if secret_present(settings.namespace, name):
            copy_secret(settings.namespace, name, reference, name)


def _store_state(workspace_id, state, provider_name, project_name, project_id, settings) -> None:
    with session_scope() as session:
        workspace = session.get(Workspace, workspace_id)
        if workspace is None:
            return
        workspace.provider = provider_name
        workspace.status = state.status
        workspace.error = state.error
        workspace.pod_name = state.pod_name
        workspace.pvc_name = state.pvc_name
        workspace.service_name = state.service_name
        detail = dict(state.detail or {})
        detail["url"] = state.url
        detail["t3_url"] = state.t3_url
        detail["t3_host"] = state.t3_url.replace("https://", "") if state.t3_url else ""
        workspace.detail = detail

    event = {
        "ready": "workspace.ready",
        "failed": "workspace.failed",
        "provisioning": "workspace.provisioning",
        "stopped": "workspace.stopped",
    }.get(state.status, "workspace.progress")
    publish_sync(
        event,
        message=f"{project_name}: {state.status}",
        project_id=project_id,
        payload={"reference": state.reference, "pod": state.pod_name, "status": state.status},
    )


def _workspace_by_id(workspace_id: str) -> dict[str, Any]:
    with session_scope() as session:
        workspace = session.get(Workspace, workspace_id)
        if workspace is None:
            raise NotFound(f"no workspace {workspace_id}")
        return _workspace_detail(workspace)


def workspace_action(project_name_or_id: str, action: str) -> dict[str, Any]:
    """start | stop | restart | refresh | reapply."""
    settings = get_settings()
    with session_scope() as session:
        project = get_project(session, project_name_or_id)
        workspace = primary_workspace(session, project.id)
        workspace_id = workspace.id
        reference = workspace.reference
        project_id = project.id
        provider = get_workspace_provider(project.workspace_provider or None, settings)

    if action == "start":
        publish_sync("workspace.starting", message=f"starting {reference}", project_id=project_id)
        state = provider.start(reference)
    elif action == "stop":
        provider.stop(reference)
        publish_sync("workspace.stopped", message=f"stopped {reference}", project_id=project_id)
        state = provider.status(reference)
    elif action == "restart":
        state = provider.restart(reference)
    elif action in {"refresh", "status"}:
        state = provider.status(reference)
    elif action == "reapply":
        return provision_workspace(project_id, force=True)
    else:
        raise Conflict(f"unknown workspace action {action!r}")

    _store_state(workspace_id, state, provider.name, project_name_or_id, project_id, settings)
    # The workspace provider is chosen from the project row, so a stored
    # provider that no longer exists must be visible rather than silently
    # swapped.
    with session_scope() as session:
        workspace = session.get(Workspace, workspace_id)
        workspace.provider = provider.name
    return _workspace_by_id(workspace_id)


def workspace_logs(project_name_or_id: str, tail: int = 200) -> str:
    settings = get_settings()
    with session_scope() as session:
        project = get_project(session, project_name_or_id)
        workspace = primary_workspace(session, project.id)
        reference = workspace.reference
        provider = get_workspace_provider(project.workspace_provider or None, settings)
    return provider.get_logs(reference, tail=tail)


def workspace_exec(project_name_or_id: str, command: list[str]) -> dict[str, Any]:
    settings = get_settings()
    with session_scope() as session:
        project = get_project(session, project_name_or_id)
        workspace = primary_workspace(session, project.id)
        reference = workspace.reference
        provider = get_workspace_provider(project.workspace_provider or None, settings)
    result = provider.execute(reference, command)
    return {
        "command": result.command,
        "exit_code": result.exit_code,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


# --- files -------------------------------------------------------------------
#
# Hand-editing from the dashboard (one UI over every project, whatever the
# workspace provider). All four operations resolve the project's own provider
# and jail paths to that provider's repository root: a checkout workspace *is*
# its repo under /projects/<name>, a DevPod workspace keeps its checkout at
# <root>/.fleet/repo. Nothing here takes an absolute host path.

#: Refuse to read more than this through the API in one call.
MAX_FILE_BYTES = 200_000
#: Cap a single directory listing so `/` can never page the world.
MAX_LIST_ENTRIES = 500

#: Prefixes the editor may list and read but never write: git's own metadata
#: and fleet's own state (including the gateway token and credentials).
READ_ONLY_PREFIXES = (".git/", ".fleet/")


def _safe_relpath(raw: str | None) -> str:
    """Jail a UI-supplied path inside the repository.

    Leading slashes are stripped (an absolute path becomes relative), `.`
    segments are dropped, and any `..` is a conflict rather than a
    best-effort clean: silently resolving `a/../..` is how an editor reads
    outside the repo it was opened on.
    """
    candidate = (raw or "").strip().replace("\x00", "")
    candidate = candidate.strip("/")
    if not candidate or candidate == ".":
        return ""
    parts: list[str] = []
    for part in candidate.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise Conflict(f"{raw!r} escapes the repository")
        parts.append(part)
    if not parts:
        return ""
    rel = "/".join(parts)
    if len(rel) > 512:
        raise Conflict("path is too long")
    return rel


def _resolve_repo(project_name_or_id: str):
    """Return (provider, reference, repo_root, project_id) for a project."""
    settings = get_settings()
    with session_scope() as session:
        project = get_project(session, project_name_or_id)
        workspace = primary_workspace(session, project.id)
        reference = workspace.reference
        project_id = project.id
        provider = get_workspace_provider(project.workspace_provider or None, settings)
        root = provider.layout(reference).repo_path
    return provider, reference, root, project_id


def list_files(project_name_or_id: str, path: str = "") -> dict[str, Any]:
    import shlex

    provider, reference, root, _ = _resolve_repo(project_name_or_id)
    rel = _safe_relpath(path)
    target = f"{root}/{rel}" if rel else root
    result = provider.execute(
        reference,
        [
            "/bin/bash",
            "-lc",
            f"set -u; target={shlex.quote(target)}; "
            'test -d "$target" || { echo NOT_A_DIRECTORY; exit 3; }; '
            f"find \"$target\" -mindepth 1 -maxdepth 1 -printf '%y\\t%s\\t%f\\n' "
            f"| sort -t \"$(printf '\\t')\" -k3 | head -n {MAX_LIST_ENTRIES + 1}",
        ],
    )
    if result.exit_code == 3 or "NOT_A_DIRECTORY" in (result.stdout or ""):
        raise NotFound(f"no directory {rel or '/'} in this project")
    if result.exit_code != 0:
        raise Conflict(f"could not list {rel or '/'}: {(result.stderr or result.stdout)[-300:]}")
    entries: list[dict[str, Any]] = []
    truncated = False
    for line in (result.stdout or "").splitlines():
        kind, _, name = (line.split("\t", 2) + ["", "", ""])[:3]
        if not name:
            continue
        if len(entries) >= MAX_LIST_ENTRIES:
            truncated = True
            break
        child = f"{rel}/{name}" if rel else name
        entries.append(
            {
                "name": name,
                "path": child,
                "is_dir": kind == "d",
                "size": int(_) if str(_).isdigit() else 0,
            }
        )
    return {"path": rel, "entries": entries, "truncated": truncated}


def read_file(project_name_or_id: str, path: str) -> dict[str, Any]:
    import shlex

    provider, reference, root, _ = _resolve_repo(project_name_or_id)
    rel = _safe_relpath(path)
    if not rel:
        raise Conflict("a file path is required")
    target = f"{root}/{rel}"
    result = provider.execute(
        reference,
        [
            "/bin/bash",
            "-lc",
            f"set -u; target={shlex.quote(target)}; "
            'test -f "$target" || { echo NOT_A_FILE; exit 3; }; '
            f'size=$(wc -c < "$target"); '
            f'if [ "$size" -gt {MAX_FILE_BYTES} ]; then echo "TOO_LARGE:$size"; exit 4; fi; '
            'cat "$target"',
        ],
    )
    if result.exit_code == 3:
        raise NotFound(f"no file {rel} in this project")
    if result.exit_code == 4:
        raise Conflict(f"{rel} is larger than the {MAX_FILE_BYTES}-byte read limit")
    if result.exit_code != 0:
        raise Conflict(f"could not read {rel}: {(result.stderr or result.stdout)[-300:]}")
    return {"path": rel, "content": result.stdout or "", "size": len(result.stdout or "")}


def write_file(project_name_or_id: str, path: str, content: str = "") -> dict[str, Any]:
    import shlex

    provider, reference, root, project_id = _resolve_repo(project_name_or_id)
    rel = _safe_relpath(path)
    if not rel:
        raise Conflict("a file path is required")
    if rel == ".git" or rel == ".fleet" or rel.startswith(READ_ONLY_PREFIXES):
        raise Conflict(f"{rel} is managed by git or fleet; it cannot be edited here")
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise Conflict("content must be text")
    if len(content.encode()) > MAX_FILE_BYTES:
        raise Conflict(f"content is larger than the {MAX_FILE_BYTES}-byte write limit")
    target = f"{root}/{rel}"
    payload = content
    result = provider.execute(
        reference,
        [
            "/bin/bash",
            "-lc",
            f"set -u; target={shlex.quote(target)}; "
            f'mkdir -p "$(dirname "$target")"; '
            f"head -c {len(payload.encode())} > \"$target\"",
        ],
        stdin_data=payload,
    )
    if result.exit_code != 0:
        raise Conflict(f"could not write {rel}: {(result.stderr or result.stdout)[-300:]}")
    publish_sync(
        "file.saved",
        message=f"{rel} saved",
        project_id=project_id,
        payload={"path": rel, "bytes": len(payload.encode())},
    )
    return {"path": rel, "bytes": len(payload.encode())}


def commit_files(project_name_or_id: str, message: str = "") -> dict[str, Any]:
    import shlex

    text = (message or "").strip() or "fleet: dashboard edit"
    if len(text) > 500:
        raise Conflict("commit message is too long")
    provider, reference, root, project_id = _resolve_repo(project_name_or_id)
    payload = text
    result = provider.execute(
        reference,
        [
            "/bin/bash",
            "-lc",
            f"set -u; root={shlex.quote(root)}; "
            'cd "$root" || { echo NO_REPO; exit 3; }; '
            f'msg=$(head -c {len(payload.encode())}); '
            'git config user.email fleet@localhost >/dev/null 2>&1 || true; '
            'git config user.name fleet >/dev/null 2>&1 || true; '
            'git add -A >/dev/null 2>&1 || true; '
            'changed=$(git status --porcelain | wc -l); '
            'commit=$(git rev-parse HEAD 2>/dev/null || echo ""); '
            'if [ "$changed" -gt 0 ]; then '
            'git commit -qm "$msg" >/dev/null 2>&1 && commit=$(git rev-parse HEAD); fi; '
            'echo "CHANGED=$changed"; echo "COMMIT=$commit"; '
            'echo "BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo none)"',
        ],
        stdin_data=payload,
    )
    if result.exit_code == 3 or "NO_REPO" in (result.stdout or ""):
        raise NotFound("this workspace has no git checkout to commit")
    if result.exit_code != 0:
        raise Conflict(f"could not commit: {(result.stderr or result.stdout)[-300:]}")
    detail = {"changed": "0", "commit": "", "branch": ""}
    for line in (result.stdout or "").splitlines():
        if line.startswith("CHANGED="):
            detail["changed"] = line.split("=", 1)[1].strip()
        elif line.startswith("COMMIT="):
            detail["commit"] = line.split("=", 1)[1].strip()
        elif line.startswith("BRANCH="):
            detail["branch"] = line.split("=", 1)[1].strip()
    if detail["commit"]:
        publish_sync(
            "commit.created",
            message=f"commit {detail['commit'][:12]} on {detail['branch']} (dashboard)",
            project_id=project_id,
            payload={"commit": detail["commit"], "branch": detail["branch"]},
        )
    return {
        "branch": detail["branch"],
        "commit": detail["commit"],
        "changed_files": detail["changed"],
    }


# --- credentials -------------------------------------------------------------


def store_credential_value(project_slug: str, name: str, value: str) -> None:
    """Write a credential value to a Kubernetes Secret. Never logged."""
    settings = get_settings()
    upsert_secret(
        settings.namespace,
        f"{project_slug}-credentials",
        {name: value},
        {**settings.labels, "fleet.io/project": project_slug},
    )


def credential_status(project_name_or_id: str) -> list[dict[str, Any]]:
    settings = get_settings()
    with session_scope() as session:
        project = get_project(session, project_name_or_id)
        credentials = (
            session.execute(select(Credential).where(Credential.project_id == project.id))
            .scalars()
            .all()
        )
        out = []
        for credential in credentials:
            out.append(
                {
                    "name": credential.name,
                    "secretRef": {"name": credential.secret_name, "key": credential.key},
                    "envVar": credential.env_var,
                    "allowedAgents": credential.allowed_agents,
                    # Presence and key names are safe to show; values are not.
                    "provisioned": secret_present(settings.namespace, credential.secret_name),
                    "keys": secret_keys(settings.namespace, credential.secret_name),
                }
            )
        return out


def set_credential(
    project_name_or_id: str, name: str, value: str, *, key: str = "token"
) -> dict[str, Any]:
    with session_scope() as session:
        project = get_project(session, project_name_or_id)
        slug = slugify(project.name)
        credential = session.execute(
            select(Credential).where(Credential.project_id == project.id, Credential.name == name)
        ).scalar_one_or_none()
        if credential is None:
            credential = Credential(
                project_id=project.id,
                name=name,
                secret_name=f"{slug}-credentials",
                key=key,
                env_var=f"{name.upper()}_TOKEN",
            )
            session.add(credential)
            session.flush()
        out = {
            "name": credential.name,
            "secretRef": {"name": credential.secret_name, "key": credential.key},
            "envVar": credential.env_var,
        }
    store_credential_value(slug, name, value)
    return {**out, "valueStored": True}


def remove_credential(project_name_or_id: str, name: str) -> None:
    """Forget a credential's metadata.

    The Kubernetes Secret is deliberately left alone: `set_credential` writes
    one secret per project holding every credential key, so deleting the row
    must not take the other credentials with it.
    """
    with session_scope() as session:
        project = get_project(session, project_name_or_id)
        credential = session.execute(
            select(Credential).where(Credential.project_id == project.id, Credential.name == name)
        ).scalar_one_or_none()
        if credential is None:
            raise NotFound(f"no credential {name!r}")
        session.delete(credential)


# --- agents ------------------------------------------------------------------


def create_agent(
    project_name_or_id: str, payload: dict[str, Any], *, start: bool = True
) -> dict[str, Any]:
    settings = get_settings()
    name = str(payload.get("name") or "").strip()
    if not name:
        raise Conflict("an agent needs a name")

    with session_scope() as session:
        project = get_project(session, project_name_or_id)
        workspace = primary_workspace(session, project.id)
        existing = session.execute(
            select(Agent).where(Agent.project_id == project.id, Agent.name == name)
        ).scalar_one_or_none()
        if existing is not None:
            raise Conflict(f"agent {name!r} already exists in {project.name}")
        agent = Agent(
            project_id=project.id,
            workspace_id=workspace.id,
            name=name,
            provider=payload.get("provider", "opencode"),
            role=payload.get("role", ""),
            model=payload.get("model") or settings.llm_default_model,
            status="created",
            worktree=payload.get("worktree") or f"{slugify(name)}",
            config={"workspace_reference": workspace.reference, **(payload.get("config") or {})},
        )
        session.add(agent)
        session.flush()
        agent_id, project_id = agent.id, project.id
        detail = _agent_detail(agent)

    publish_sync(
        "agent.created",
        message=f"agent {name} ({detail['provider']}) in {project_name_or_id}",
        project_id=project_id,
        agent_id=agent_id,
        payload={"provider": detail["provider"]},
    )
    if start:
        try:
            start_agent(project_id, agent_id)
            detail = get_agent_detail(project_id, agent_id)
        except Exception as exc:  # noqa: BLE001 - reported through status
            with session_scope() as session:
                agent = session.get(Agent, agent_id)
                agent.status = "error"
                agent.config = {
                    **(agent.config or {}),
                    "start_error": f"{type(exc).__name__}: {exc}",
                }
    return detail


def start_agent(project_id: str, agent_id: str) -> str:
    settings = get_settings()
    with session_scope() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise NotFound(f"no agent {agent_id}")
        agent_spec = _agent_spec(session, agent, settings)
        # The project's provider, not the deployment default: a checkout
        # agent probed through the DevPod provider reports "opencode is not
        # installed" because the layout (and the exec target) is wrong.
        project = session.get(Project, agent.project_id)
        provider_name = project.workspace_provider if project else None

    provider = get_agent_provider(
        agent_spec.provider, get_workspace_provider(provider_name, settings)
    )
    status = provider.start(agent_spec)
    endpoint = provider.endpoint(agent_spec)
    with session_scope() as session:
        agent = session.get(Agent, agent_id)
        agent.status = status
        agent.config = {**(agent.config or {}), "endpoint": endpoint}
    publish_sync(
        "agent.started",
        message=f"agent {agent_spec.name} {status}",
        project_id=agent_spec.project_id,
        agent_id=agent_id,
        payload={"provider": provider.name, "endpoint": endpoint},
    )
    return status


def stop_agent(project_id: str, agent_id: str) -> str:
    settings = get_settings()
    with session_scope() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise NotFound(f"no agent {agent_id}")
        spec = _agent_spec(session, agent, settings)
        project = session.get(Project, agent.project_id)
        provider_name = project.workspace_provider if project else None
    provider = get_agent_provider(spec.provider, get_workspace_provider(provider_name, settings))
    status = provider.stop(spec)
    with session_scope() as session:
        agent = session.get(Agent, agent_id)
        agent.status = status
    publish_sync(
        "agent.stopped",
        message=f"agent {spec.name} stopped",
        project_id=spec.project_id,
        agent_id=agent_id,
    )
    return status


def _agent_spec(session, agent: Agent, settings: Settings):  # noqa: ARG001
    from .agents import AgentSpec

    project = session.get(Project, agent.project_id)
    workspace = primary_workspace(session, agent.project_id)
    return AgentSpec(
        agent_id=agent.id,
        name=agent.name,
        project_id=agent.project_id,
        project_name=project.name,
        workspace_reference=workspace.reference,
        provider=agent.provider,
        role=agent.role,
        model=agent.model,
        worktree=agent.worktree,
        config=agent.config or {},
    )


def agent_logs(project_id: str, agent_id: str, *, task_id: str = "", tail: int = 200) -> str:
    with session_scope() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise NotFound(f"no agent {agent_id}")
        spec = _agent_spec(session, agent, get_settings())
        from .models import Project

        project = session.get(Project, agent.project_id)
        provider_name = project.workspace_provider if project else None
    provider = get_agent_provider(
        spec.provider, get_workspace_provider(provider_name, get_settings())
    )
    return provider.get_logs(spec, task_id=task_id, tail=tail)


def agent_action(project_id: str, agent_id: str, action: str) -> str:
    if action == "start":
        return start_agent(project_id, agent_id)
    if action == "stop":
        return stop_agent(project_id, agent_id)
    raise Conflict(f"unknown agent action {action!r}")


def get_agent_detail(project_id: str, agent_id: str) -> dict[str, Any]:
    with session_scope() as session:
        agent = session.get(Agent, agent_id)
        if agent is None:
            raise NotFound(f"no agent {agent_id}")
        return _agent_detail(agent)


def list_agents(project_name_or_id: str) -> list[dict[str, Any]]:
    with session_scope() as session:
        project = get_project(session, project_name_or_id)
        agents = (
            session.execute(
                select(Agent).where(Agent.project_id == project.id).order_by(Agent.created_at)
            )
            .scalars()
            .all()
        )
        return [_agent_detail(a) for a in agents]


# --- tasks -------------------------------------------------------------------


def create_task(project_name_or_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from . import runner

    prompt = str(payload.get("prompt") or "").strip()
    if not prompt:
        raise Conflict("a task needs a prompt")

    with session_scope() as session:
        project = get_project(session, project_name_or_id)
        workspace = primary_workspace(session, project.id)
        agent_ref = payload.get("agent")
        if agent_ref:
            agent = get_agent(session, project.id, agent_ref)
        else:
            agent = (
                session.execute(
                    select(Agent).where(Agent.project_id == project.id).order_by(Agent.created_at)
                )
                .scalars()
                .first()
            )
            if agent is None:
                raise Conflict(f"project {project.name} has no agents to assign the task to")
        if agent.provider in {"t3code"}:
            raise Conflict(
                f"agent {agent.name!r} uses the {agent.provider} provider, which is a control "
                "surface rather than a task runner; assign the task to an opencode agent"
            )
        task = Task(
            project_id=project.id,
            agent_id=agent.id,
            workspace_id=workspace.id,
            prompt=prompt,
            priority=payload.get("priority", "normal"),
            status="queued",
        )
        session.add(task)
        session.flush()
        task_id, project_id = task.id, project.id
        agent.status = "busy"
        agent.current_task_id = task.id
        detail = _task_detail(task)

    publish_sync(
        "task.created",
        message=f"task queued for {agent_ref or 'the first agent'}",
        project_id=project_id,
        agent_id=detail["agent_id"],
        task_id=task_id,
        payload={"priority": detail["priority"]},
    )
    runner.submit(task_id)
    return detail


def _task_detail(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "project_id": task.project_id,
        "agent_id": task.agent_id,
        "workspace_id": task.workspace_id,
        "prompt": task.prompt,
        "status": task.status,
        "priority": task.priority,
        "result": task.result,
        "error": task.error,
        "git_branch": task.git_branch,
        "git_commit": task.git_commit,
        "worktree": task.worktree,
        "createdAt": task.created_at.isoformat() if task.created_at else None,
        "startedAt": task.started_at.isoformat() if task.started_at else None,
        "completedAt": task.completed_at.isoformat() if task.completed_at else None,
    }


def get_task(task_id: str) -> dict[str, Any]:
    with session_scope() as session:
        task = session.get(Task, task_id)
        if task is None:
            raise NotFound(f"no task {task_id}")
        return _task_detail(task)


def list_tasks(project_name_or_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    with session_scope() as session:
        project = get_project(session, project_name_or_id)
        tasks = (
            session.execute(
                select(Task)
                .where(Task.project_id == project.id)
                .order_by(Task.created_at.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return [_task_detail(t) for t in tasks]


def cancel_task(task_id: str) -> dict[str, Any]:
    from . import runner

    with session_scope() as session:
        task = session.get(Task, task_id)
        if task is None:
            raise NotFound(f"no task {task_id}")
        if task.status in {"completed", "failed", "cancelled"}:
            raise Conflict(f"task {task_id} is already {task.status}")
        task.status = "cancelled"
        task.error = "cancelled by request"
        from datetime import UTC, datetime

        task.completed_at = datetime.now(UTC)
        project_id, agent_id = task.project_id, task.agent_id

    runner.cancel(task_id)
    publish_sync(
        "task.cancelled",
        message=f"task {task_id} cancelled",
        project_id=project_id,
        agent_id=agent_id,
        task_id=task_id,
    )
    return get_task(task_id)


# --- introspection -----------------------------------------------------------


def provider_report() -> dict[str, Any]:
    from .workspaces import describe_providers

    settings = get_settings()
    workspaces = get_workspace_provider(None, settings)
    return {
        "workspace_providers": describe_providers(settings),
        "agent_providers": describe_agent_providers(workspaces),
        "selected_workspace_provider": workspaces.name,
        "configured_workspace_provider": settings.workspace_provider,
    }
