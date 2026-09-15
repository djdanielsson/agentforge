"""Agent-provider registry (SPEC §9, §46)."""

from __future__ import annotations

from ..workspaces.base import ProviderError, WorkspaceProvider
from .base import AgentError, AgentProvider, AgentSpec, TaskOutcome, TaskRequest
from .opencode import OpenCodeProvider
from .shell import ShellAgentProvider
from .t3code import T3CodeProvider

AGENT_PROVIDERS: dict[str, type[AgentProvider]] = {
    "opencode": OpenCodeProvider,
    "t3code": T3CodeProvider,
    "shell": ShellAgentProvider,
}

DEFAULT_AGENT_PROVIDER = "opencode"


def get_agent_provider(name: str | None, workspaces: WorkspaceProvider) -> AgentProvider:
    requested = (name or DEFAULT_AGENT_PROVIDER).strip().lower()
    cls = AGENT_PROVIDERS.get(requested)
    if cls is None:
        raise AgentError(
            f"unknown agent provider {requested!r}; known: {', '.join(sorted(AGENT_PROVIDERS))}"
        )
    return cls(workspaces)


def describe_agent_providers(workspaces: WorkspaceProvider) -> list[dict]:
    out = []
    for name, cls in AGENT_PROVIDERS.items():
        try:
            out.append({"name": name, "capabilities": cls(workspaces).capabilities()})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": name, "error": f"{type(exc).__name__}: {exc}"})
    return out


__all__ = [
    "AGENT_PROVIDERS",
    "DEFAULT_AGENT_PROVIDER",
    "AgentError",
    "AgentProvider",
    "AgentSpec",
    "ProviderError",
    "TaskOutcome",
    "TaskRequest",
    "describe_agent_providers",
    "get_agent_provider",
]
