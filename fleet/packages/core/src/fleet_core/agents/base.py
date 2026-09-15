"""The agent-provider interface (SPEC §9).

An agent provider drives *some* coding agent inside a workspace. The control
plane never implements a model itself (SPEC §43): it starts, tasks, monitors and
stops whatever the provider wraps.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class AgentError(RuntimeError):
    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


@dataclass
class AgentSpec:
    """Everything an agent provider needs, resolved by the control plane."""

    agent_id: str
    name: str
    project_id: str
    project_name: str
    workspace_reference: str
    provider: str = "opencode"
    role: str = ""
    model: str = "local-coder"
    worktree: str = ""
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskRequest:
    task_id: str
    prompt: str
    worktree: str = ""
    branch: str = ""
    timeout: int = 900


@dataclass
class TaskOutcome:
    status: str  # completed | failed | cancelled
    output: str = ""
    error: str = ""
    exit_code: int = 0
    git_branch: str = ""
    git_commit: str = ""
    files_changed: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


class AgentProvider(ABC):
    name: str = "unknown"
    #: Whether this provider can turn a prompt into work without a human.
    executes_tasks: bool = True
    #: Whether a human-facing control surface (T3 Code) is part of this provider.
    provides_ui: bool = False

    @abstractmethod
    def start(self, spec: AgentSpec) -> str:
        """Prepare the agent. Returns the resulting status string."""

    @abstractmethod
    def execute_task(self, spec: AgentSpec, request: TaskRequest) -> TaskOutcome: ...

    def stop(self, spec: AgentSpec) -> str:
        return "stopped"

    def cancel_task(self, spec: AgentSpec, request: TaskRequest) -> bool:
        """Best-effort cancellation. Returning False means it could not stop it."""
        return False

    def get_status(self, spec: AgentSpec) -> str:
        return "unknown"

    def get_logs(self, spec: AgentSpec, *, task_id: str = "", tail: int = 200) -> str:
        return ""

    def send_input(self, spec: AgentSpec, text: str) -> bool:
        """Interactive input. Most headless providers cannot accept any."""
        return False

    def endpoint(self, spec: AgentSpec) -> str:
        """A URL for a human to watch or drive this agent, if it has one."""
        return ""

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "executes_tasks": self.executes_tasks,
            "provides_ui": self.provides_ui,
            "cancel": True,
            "send_input": False,
        }
