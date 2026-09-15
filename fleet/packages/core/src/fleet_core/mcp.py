"""The fleet control plane as an MCP server (SPEC §6, T3-INTEGRATION §A).

T3 Code has no plugin API. The extension surfaces it does have are MCP servers,
providers and environments, so "drive fleet from T3's chat" means exactly this:
a tool surface the agent session inside T3 can call.

This module is the *tool surface*, not the transport — it maps tool names onto
the same `service` functions the HTTP API calls, so there is no second code path
to keep honest. `fleet_api.routers.mcp` speaks JSON-RPC over Streamable HTTP to
it.

A tool that cannot do what it was asked returns `isError: true` with the reason
in the content, rather than raising: the model on the other end can read a
failure and try something else, and a 500 tells it nothing.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import service

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "fleet-control-plane"
SERVER_VERSION = "0.1.0"

#: The directories a checkout-mode project lives in, as the tool schema says.
CHECKOUT_HINT = (
    "checkout = the project is a directory inside the shared T3 environment "
    "(/projects/<name>); devpod = the project gets its own Kubernetes namespace"
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_projects",
        "title": "List fleet projects",
        "description": (
            "List every project the fleet control plane knows, with its workspace "
            "provider, workspace status and agent count. Call this first: a project "
            "must exist in fleet before an agent can be given work in it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "create_project",
        "title": "Create a fleet project",
        "description": (
            "Create a project and provision its workspace. Returns immediately with "
            "status 'pending' or 'provisioning'; provisioning is reconciled in the "
            "background, so poll workspace_status. " + CHECKOUT_HINT
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Unique project name."},
                "description": {"type": "string"},
                "repository_url": {
                    "type": "string",
                    "description": "Git URL to clone into the workspace. Optional.",
                },
                "repository_branch": {"type": "string", "default": "main"},
                "workspace_provider": {
                    "type": "string",
                    "enum": ["checkout", "devpod", "kubernetes"],
                    "description": CHECKOUT_HINT,
                },
                "cpu": {"type": "string"},
                "memory": {"type": "string"},
                "storage": {"type": "string"},
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_agents",
        "title": "List a project's agents",
        "description": "Every agent in a project, with its provider, status and endpoint.",
        "inputSchema": {
            "type": "object",
            "properties": {"project": {"type": "string", "description": "Project name or id."}},
            "required": ["project"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_agent",
        "title": "Create an agent in a project",
        "description": (
            "Add an agent to a project. An 'opencode' agent runs tasks; a 't3code' "
            "agent is a control surface, not a task runner."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "name": {"type": "string"},
                "provider": {"type": "string", "enum": ["opencode", "shell", "t3code"]},
                "role": {"type": "string"},
                "model": {"type": "string", "description": "Logical gateway model alias."},
            },
            "required": ["project", "name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_task",
        "title": "Submit a task to an agent",
        "description": (
            "Queue a prompt for a project's agent. The agent works in its own git "
            "worktree and answers through the fleet gateway, with usage attributed. "
            "Returns the task immediately; poll task_status for the result."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "prompt": {"type": "string", "description": "What the agent should do."},
                "agent": {
                    "type": "string",
                    "description": "Agent name or id; defaults to the project's first agent.",
                },
                "priority": {"type": "string", "enum": ["low", "normal", "high"]},
            },
            "required": ["project", "prompt"],
            "additionalProperties": False,
        },
    },
    {
        "name": "task_status",
        "title": "Read a task",
        "description": (
            "One task: status, the agent's answer, any error, and the git branch and "
            "commit it produced."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"task": {"type": "string", "description": "Task id."}},
            "required": ["task"],
            "additionalProperties": False,
        },
    },
    {
        "name": "workspace_status",
        "title": "Read a project's workspace",
        "description": (
            "The project's workspace: provider, reference, status, pod, url and the "
            "T3 url when the provider publishes one. Use it to watch a project "
            "become ready."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"project": {"type": "string"}},
            "required": ["project"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_tasks",
        "title": "List a project's tasks",
        "description": "A project's recent tasks, newest first.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
            },
            "required": ["project"],
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = [tool["name"] for tool in TOOLS]


def _text(payload: Any) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}],
        "structuredContent": payload if isinstance(payload, dict) else {"result": payload},
    }


def _error(message: str, **detail: Any) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"error": message, **detail}, indent=2, default=str),
            }
        ],
        "isError": True,
    }


def _project_payload(detail: dict[str, Any]) -> dict[str, Any]:
    """The useful part of a project row, without the whole workspace document."""
    return {
        "id": detail["id"],
        "name": detail["name"],
        "repository": detail["repository"],
        "workspace": detail["workspace"],
        "workspaces": [
            {
                "provider": w["provider"],
                "reference": w["reference"],
                "status": w["status"],
                "error": w["error"],
                "pod": w["pod"],
                "url": w["url"],
                "t3_url": w["t3_url"],
            }
            for w in detail["workspaces"]
        ],
        "agents": [
            {"name": a["name"], "provider": a["provider"], "status": a["status"]}
            for a in detail["agents"]
        ],
    }


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run a tool and return an MCP tool result. Never raises for tool-level failure."""
    arguments = arguments or {}
    try:
        handler = _HANDLERS.get(name)
        if handler is None:
            return _error(f"unknown tool {name!r}", known=TOOL_NAMES)
        return handler(arguments)
    except service.NotFound as exc:
        return _error(str(exc))
    except service.Conflict as exc:
        return _error(str(exc))
    except Exception as exc:  # noqa: BLE001 - a tool result, not a crash
        log.exception("mcp tool %s failed", name)
        return _error(f"{type(exc).__name__}: {exc}", tool=name)


def _require(arguments: dict[str, Any], key: str) -> str:
    value = str(arguments.get(key) or "").strip()
    if not value:
        raise service.Conflict(f"{key!r} is required")
    return value


def _list_projects(arguments: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    projects = service.list_projects()
    return _text({"count": len(projects), "items": [_project_payload(p) for p in projects]})


def _create_project(arguments: dict[str, Any]) -> dict[str, Any]:
    name = _require(arguments, "name")
    payload: dict[str, Any] = {"name": name}
    if arguments.get("description"):
        payload["description"] = arguments["description"]
    if arguments.get("repository_url"):
        payload["repository"] = {
            "url": arguments["repository_url"],
            "branch": arguments.get("repository_branch") or "main",
        }
    workspace: dict[str, Any] = {}
    if arguments.get("workspace_provider"):
        workspace["provider"] = arguments["workspace_provider"]
    for key in ("cpu", "memory", "storage"):
        if arguments.get(key):
            workspace[key] = arguments[key]
    if workspace:
        payload["workspace"] = workspace
    return _text(_project_payload(service.create_project(payload)))


def _list_agents(arguments: dict[str, Any]) -> dict[str, Any]:
    agents = service.list_agents(_require(arguments, "project"))
    return _text({"count": len(agents), "items": agents})


def _create_agent(arguments: dict[str, Any]) -> dict[str, Any]:
    project = _require(arguments, "project")
    payload = {"name": _require(arguments, "name")}
    for key in ("provider", "role", "model"):
        if arguments.get(key):
            payload[key] = arguments[key]
    return _text(service.create_agent(project, payload))


def _submit_task(arguments: dict[str, Any]) -> dict[str, Any]:
    project = _require(arguments, "project")
    payload: dict[str, Any] = {"prompt": _require(arguments, "prompt")}
    if arguments.get("agent"):
        payload["agent"] = arguments["agent"]
    if arguments.get("priority"):
        payload["priority"] = arguments["priority"]
    return _text(service.create_task(project, payload))


def _task_status(arguments: dict[str, Any]) -> dict[str, Any]:
    return _text(service.get_task(_require(arguments, "task")))


def _workspace_status(arguments: dict[str, Any]) -> dict[str, Any]:
    detail = service.get_project_detail(_require(arguments, "project"))
    if not detail["workspaces"]:
        return _error(f"project {detail['name']!r} has no workspace")
    return _text(detail["workspaces"][0])


def _list_tasks(arguments: dict[str, Any]) -> dict[str, Any]:
    project = _require(arguments, "project")
    limit = int(arguments.get("limit") or 20)
    tasks = service.list_tasks(project, limit=max(1, min(limit, 100)))
    return _text({"count": len(tasks), "items": tasks})


_HANDLERS = {
    "list_projects": _list_projects,
    "create_project": _create_project,
    "list_agents": _list_agents,
    "create_agent": _create_agent,
    "submit_task": _submit_task,
    "task_status": _task_status,
    "workspace_status": _workspace_status,
    "list_tasks": _list_tasks,
}
