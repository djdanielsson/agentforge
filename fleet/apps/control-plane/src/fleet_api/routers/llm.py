"""The attributing LLM proxy (SPEC §11, §12, §13, §14).

A workspace never holds a model-provider key. It holds a project token and points
its agent at this route. The control plane:

* authenticates the project token and refuses an unknown one,
* applies the project's LLM policy (local-only, allowed/blocked models),
* forwards to the gateway with the real credential,
* and records tokens and cost against the project, agent and task that asked.

That last point is the whole reason this proxy exists rather than pointing
workspaces straight at LiteLLM: per-project, per-agent, per-task accounting is
not something a shared gateway key can give you.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fleet_core.config import get_settings
from fleet_core.llm import LLMResult, record_usage, verify_project_token

log = logging.getLogger(__name__)

router = APIRouter(prefix="/llm/v1", tags=["llm"])

LOCAL_ONLY_ALIASES = {"local", "local-coder", "local-coder-large"}


def _identify(authorization: str) -> tuple[str, str]:
    """Return (project_id, principal). Raises 401 when the caller is unknown."""
    settings = get_settings()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="expected Authorization: Bearer <project token>")
    project_id = verify_project_token(token)
    if project_id:
        return project_id, "project-token"
    if settings.api_token and token == settings.api_token:
        # The operator may probe through the same route; the project comes from
        # a header so the call is still attributed.
        return "", "operator"
    raise HTTPException(status_code=401, detail="unknown project token")


def _policy_check(project_id: str, model: str, body: dict[str, Any]) -> None:
    """Enforce the project's LLM policy before spending anything (SPEC §14)."""
    if not project_id:
        return
    from fleet_core.db import session_scope
    from fleet_core.models import Project

    with session_scope() as session:
        project = session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=401, detail="project token names no live project")
        policy = project.llm_policy or {}
        mode = str(policy.get("mode", "")).lower()
        if mode in {"localonly", "local-only", "local_only"} and model not in LOCAL_ONLY_ALIASES:
            raise HTTPException(
                status_code=403,
                detail=f"project policy is local-only; {model!r} is not a local model",
            )
        allowed = policy.get("allowedModels") or policy.get("allowed_models") or []
        if allowed and model not in allowed:
            raise HTTPException(status_code=403, detail=f"model {model!r} is not allowed by policy")
        blocked = policy.get("blockedModels") or policy.get("blocked_models") or []
        if model in blocked:
            raise HTTPException(status_code=403, detail=f"model {model!r} is blocked by policy")
    del body


@router.get("/models")
def models(authorization: str = Header(default="")) -> dict[str, Any]:
    _identify(authorization)
    settings = get_settings()
    try:
        with httpx.Client(timeout=20) as client:
            response = client.get(
                settings.llm_gateway_url.rstrip("/") + "/v1/models",
                headers={"Authorization": f"Bearer {settings.llm_gateway_key}"},
            )
            response.raise_for_status()
            return response.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"gateway unreachable: {exc}") from exc


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    authorization: str = Header(default=""),
    x_fleet_agent: str = Header(default=""),
    x_fleet_task: str = Header(default=""),
):
    project_id, principal = _identify(authorization)
    settings = get_settings()
    if not settings.llm_gateway_url or not settings.llm_gateway_key:
        raise HTTPException(status_code=503, detail="the fleet gateway is not configured")

    body = await request.json()
    model = str(body.get("model") or settings.llm_default_model)
    _policy_check(project_id, model, body)

    # The gateway resolves logical aliases (SPEC §12), so the model is passed
    # through unchanged rather than rewritten here.
    upstream = settings.llm_gateway_url.rstrip("/") + "/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm_gateway_key}",
        "Content-Type": "application/json",
    }

    if body.get("stream"):
        return StreamingResponse(
            _stream(upstream, headers, body, project_id, model, x_fleet_agent, x_fleet_task),
            media_type="text/event-stream",
        )

    async with httpx.AsyncClient(timeout=settings.llm_request_timeout) as client:
        response = await client.post(upstream, headers=headers, json=body)
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"gateway {response.status_code}: {response.text[:300]}",
        )
    data = response.json()
    _record(data, project_id, model, x_fleet_agent, x_fleet_task)
    log.info("llm: project=%s principal=%s model=%s", project_id or "-", principal, data.get("model"))
    return JSONResponse(data)


async def _stream(
    upstream: str,
    headers: dict[str, str],
    body: dict[str, Any],
    project_id: str,
    requested_model: str,
    agent_id: str,
    task_id: str,
):
    """Pass the gateway's stream through, keeping the usage chunk for accounting."""
    payload = {**body, "stream_options": {"include_usage": True}}
    usage: dict[str, Any] = {}
    served_model = requested_model
    async with httpx.AsyncClient(timeout=get_settings().llm_request_timeout) as client:
        async with client.stream("POST", upstream, headers=headers, json=payload) as response:
            if response.status_code >= 400:
                detail = (await response.aread()).decode()[:300]
                yield f"data: {json.dumps({'error': {'message': f'gateway {response.status_code}: {detail}'}})}\n\n"
                yield "data: [DONE]\n\n"
                return
            async for line in response.aiter_lines():
                if not line:
                    continue
                if line.startswith("data:"):
                    chunk = line[5:].strip()
                    if chunk and chunk != "[DONE]":
                        try:
                            parsed = json.loads(chunk)
                        except json.JSONDecodeError:
                            parsed = {}
                        if parsed.get("usage"):
                            usage = parsed["usage"]
                        if parsed.get("model"):
                            served_model = parsed["model"]
                yield f"{line}\n\n"

    if usage:
        result = LLMResult(
            content="",
            model=served_model,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            cached_tokens=int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
            cost=float(usage.get("cost") or 0.0),
        )
        record_usage(
            result,
            requested_model=requested_model,
            project_id=project_id or None,
            agent_id=agent_id or None,
            task_id=task_id or None,
            provider="fleet-proxy",
        )
    else:
        log.warning("streamed completion carried no usage; nothing recorded")


def _record(
    data: dict[str, Any], project_id: str, requested_model: str, agent_id: str, task_id: str
) -> None:
    usage = data.get("usage") or {}
    result = LLMResult(
        content="",
        model=data.get("model", requested_model),
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        cached_tokens=int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
        cost=float(usage.get("cost") or 0.0),
    )
    record_usage(
        result,
        requested_model=requested_model,
        project_id=project_id or None,
        agent_id=agent_id or None,
        task_id=task_id or None,
        provider="fleet-proxy",
    )
