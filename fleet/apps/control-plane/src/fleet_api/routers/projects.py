"""Projects, workspaces and credentials (SPEC §22)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fleet_core import service
from fleet_core.workspaces import ProviderError

from ..security import require_token

router = APIRouter(prefix="/api/v1", tags=["projects"], dependencies=[Depends(require_token)])


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, service.NotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, service.Conflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ProviderError):
        return HTTPException(status_code=502, detail={"error": str(exc), **(exc.detail or {})})
    raise exc


@router.get("/projects")
def list_projects() -> dict[str, Any]:
    projects = service.list_projects()
    return {"items": projects, "count": len(projects)}


@router.post("/projects", status_code=201)
def create_project(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return service.create_project(payload)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.get("/projects/{project}")
def get_project(project: str) -> dict[str, Any]:
    try:
        return service.get_project_detail(project)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.delete("/projects/{project}", status_code=202)
def delete_project(project: str) -> dict[str, Any]:
    try:
        service.delete_project(project)
        return {"deleted": project}
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.get("/projects/{project}/workspace")
def get_workspace(project: str) -> dict[str, Any]:
    try:
        detail = service.get_project_detail(project)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    if not detail["workspaces"]:
        raise HTTPException(status_code=404, detail="project has no workspace")
    return detail["workspaces"][0]


@router.post("/projects/{project}/workspace/exec")
def workspace_exec(project: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Run a command in the workspace.

    Exposed deliberately: it is how an operator checks isolation without a
    shell, and it is the same path an agent's provider uses.

    Declared before the generic `/{action}` route: FastAPI matches in
    declaration order, so `exec` would otherwise be read as an action name.
    """
    command = payload.get("command")
    if isinstance(command, str):
        command = ["bash", "-lc", command]
    if not isinstance(command, list) or not command:
        raise HTTPException(
            status_code=422, detail="'command' must be a string or a non-empty list"
        )
    try:
        return service.workspace_exec(project, command)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.post("/projects/{project}/workspace/{action}")
def workspace_action(project: str, action: str) -> dict[str, Any]:
    try:
        return service.workspace_action(project, action)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.get("/projects/{project}/workspace/logs")
def workspace_logs(project: str, tail: int = Query(default=200, le=2000)) -> dict[str, Any]:
    try:
        return {"logs": service.workspace_logs(project, tail=tail)}
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.get("/projects/{project}/agents")
def list_agents(project: str) -> dict[str, Any]:
    try:
        agents = service.list_agents(project)
        return {"items": agents, "count": len(agents)}
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.post("/projects/{project}/agents", status_code=201)
def create_agent(project: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return service.create_agent(project, payload)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.get("/projects/{project}/tasks")
def list_tasks(project: str, limit: int = Query(default=50, le=500)) -> dict[str, Any]:
    try:
        tasks = service.list_tasks(project, limit=limit)
        return {"items": tasks, "count": len(tasks)}
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.post("/projects/{project}/tasks", status_code=202)
def create_task(project: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return service.create_task(project, payload)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.get("/projects/{project}/usage")
def project_usage(project: str) -> dict[str, Any]:
    from fleet_core.llm import project_usage as roll_up

    try:
        detail = service.get_project_detail(project)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    return roll_up(detail["id"])


@router.get("/projects/{project}/credentials")
def list_credentials(project: str) -> dict[str, Any]:
    try:
        credentials = service.credential_status(project)
        return {"items": credentials, "count": len(credentials)}
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.put("/projects/{project}/credentials/{name}")
def set_credential(project: str, name: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Store a credential value.

    The value goes to a Kubernetes Secret; it is never echoed back by any route.
    """
    value = payload.get("value")
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=422, detail="'value' must be a non-empty string")
    try:
        return service.set_credential(project, name, value, key=payload.get("key", name))
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.delete("/projects/{project}/credentials/{name}")
def delete_credential(project: str, name: str) -> dict[str, Any]:
    try:
        service.remove_credential(project, name)
        return {"removed": name}
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
