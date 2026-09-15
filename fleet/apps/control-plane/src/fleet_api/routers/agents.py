"""Agents and tasks (SPEC §22).

Agents are addressed globally because their ids are unique across projects,
which is what lets Hermes submit a task without knowing the URL shape.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fleet_core import service
from fleet_core.agents import AgentError
from fleet_core.workspaces import ProviderError

from ..security import require_token

router = APIRouter(prefix="/api/v1", tags=["agents"], dependencies=[Depends(require_token)])


def _translate(exc: Exception) -> HTTPException:
    if isinstance(exc, service.NotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, service.Conflict):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, AgentError):
        return HTTPException(status_code=409, detail={"error": str(exc), **(exc.detail or {})})
    if isinstance(exc, ProviderError):
        return HTTPException(status_code=502, detail={"error": str(exc), **(exc.detail or {})})
    raise exc


@router.get("/agents/{agent}")
def get_agent(agent: str) -> dict[str, Any]:
    try:
        return service.get_agent_detail(_project_of(agent), agent)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


def _project_of(agent_id: str) -> str:
    from fleet_core.db import session_scope
    from fleet_core.models import Agent

    with session_scope() as session:
        found = session.get(Agent, agent_id)
        if found is None:
            raise service.NotFound(f"no agent {agent_id}")
        return found.project_id


@router.post("/agents/{agent}/start")
def start_agent(agent: str) -> dict[str, Any]:
    project_id = _project_of(agent)
    try:
        status = service.start_agent(project_id, agent)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    return service.get_agent_detail(project_id, agent) | {"status": status}


@router.post("/agents/{agent}/stop")
def stop_agent(agent: str) -> dict[str, Any]:
    project_id = _project_of(agent)
    try:
        status = service.stop_agent(project_id, agent)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
    return service.get_agent_detail(project_id, agent) | {"status": status}


@router.get("/agents/{agent}/logs")
def agent_logs(
    agent: str, task: str = Query(default=""), tail: int = Query(default=200, le=2000)
) -> dict[str, Any]:
    try:
        return {"logs": service.agent_logs(_project_of(agent), agent, task_id=task, tail=tail)}
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.post("/agents/{agent}/tasks", status_code=202)
def agent_task(agent: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Submit a task by agent id, without naming the project (SPEC §23)."""
    project_id = _project_of(agent)
    try:
        return service.create_task(project_id, {**payload, "agent": agent})
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.get("/tasks/{task}")
def get_task(task: str) -> dict[str, Any]:
    try:
        return service.get_task(task)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc


@router.post("/tasks/{task}/cancel")
def cancel_task(task: str) -> dict[str, Any]:
    try:
        return service.cancel_task(task)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc) from exc
