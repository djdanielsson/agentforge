"""MCP over Streamable HTTP (T3-INTEGRATION §A).

One endpoint, `/mcp`. The transport was chosen by observation, not preference:
OpenCode 1.18.31 (the version the fleet environments install) is an MCP *client*
through the AI SDK, and it was pointed at a logging server to see what it
actually does. It:

    POST /mcp   Accept: application/json, text/event-stream
                {"method":"initialize","params":{"protocolVersion":"2025-11-25",...}}
    POST /mcp   {"method":"notifications/initialized"}        -> 202, no body
    GET  /mcp   Accept: text/event-stream                     (optional; 404 is fine)
    POST /mcp   {"method":"tools/list"}                       -> 200 JSON

so this serves Streamable HTTP at the same path: JSON in, JSON out, 202 for
notifications, and 405 on GET (the spec allows a server that offers no
server-initiated stream; a 404 was also accepted by the client).

Auth is the control plane's own operator token, from the same
`Authorization: Bearer` header the MCP client is configured with. When the
deployment runs with no token (the testing mode), the endpoint is open like
every other route.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from fleet_core import mcp

from ..security import require_token

log = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])

#: The protocol revision we answer with when the client asks for one we do not
#: know. MCP clients negotiate down from what the server reports.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


def _result(ident: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def _error(ident: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": ident, "error": error}


def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    """Answer one JSON-RPC message. `None` means "a notification: no reply"."""
    if not isinstance(message, dict):
        return _error(None, -32600, "invalid request: expected a JSON object")
    method = message.get("method")
    ident = message.get("id")
    params = message.get("params") or {}

    if ident is None and method is not None:
        # A notification (notifications/initialized, notifications/cancelled).
        log.debug("mcp notification %s", method)
        return None

    if method == "initialize":
        requested = str(params.get("protocolVersion") or "")
        version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else mcp.PROTOCOL_VERSION
        return _result(
            ident,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": mcp.SERVER_NAME, "version": mcp.SERVER_VERSION},
                "instructions": (
                    "Fleet control plane: projects, workspaces, agents and tasks. "
                    "Agents run in a project's workspace; tasks are prompts for them."
                ),
            },
        )

    if method == "ping":
        return _result(ident, {})

    if method == "tools/list":
        return _result(ident, {"tools": mcp.TOOLS})

    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return _error(ident, -32602, "invalid params: 'arguments' must be an object")
        # A tool failure is a *result* with isError, not a JSON-RPC error: the
        # caller is a model, and it can act on a readable failure.
        return _result(ident, mcp.call_tool(name, arguments))

    if method in {"resources/list", "prompts/list"}:
        # Declared capabilities are tools-only; answer emptily rather than 404.
        key = "resources" if method.startswith("resources") else "prompts"
        return _result(ident, {key: []})

    return _error(ident, -32601, f"method not found: {method}")


@router.post("/mcp")
async def mcp_post(request: Request, _: str = Depends(require_token)) -> Response:
    """Accept one JSON-RPC message (or a batch) and answer it."""
    raw = await request.body()
    try:
        payload = json.loads(raw or b"null")
    except json.JSONDecodeError as exc:
        return JSONResponse(
            _error(None, -32700, f"parse error: {exc}"), status_code=400
        )

    batch = isinstance(payload, list)
    messages = payload if batch else [payload]
    replies = [reply for reply in (_handle(m) for m in messages) if reply is not None]

    if not replies:
        # Every message was a notification. 202 with no body is the spec's answer.
        return Response(status_code=202)

    body = replies if batch else replies[0]
    return JSONResponse(body, media_type="application/json")


@router.get("/mcp")
def mcp_get(_: str = Depends(require_token)) -> Response:
    """No server-initiated stream.

    The spec lets a server answer 405 here and requires the `Allow` header when
    it does. The client that matters accepted a 404 in testing, so this is not
    a gap — it is the stateless shape.
    """
    return Response(status_code=405, headers={"Allow": "POST, DELETE"})


@router.delete("/mcp")
def mcp_delete(_: str = Depends(require_token)) -> Response:
    """Session termination. Nothing is kept between requests, so this is a no-op."""
    return Response(status_code=204)
