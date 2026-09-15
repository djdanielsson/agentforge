"""A command agent: runs the task prompt as a shell command in the workspace.

This exists so the *control plane* can be exercised deterministically — proving
task lifecycle, isolation and event flow without depending on a model's
behaviour — and so an operator can run a one-off command through the same task
API they use for a coding agent.

It is a test and operations provider. It is not an AI coding agent, and the API
reports it as such.
"""

from __future__ import annotations

from ..workspaces.base import WorkspaceProvider
from .base import AgentProvider, AgentSpec, TaskOutcome, TaskRequest


class ShellAgentProvider(AgentProvider):
    name = "shell"

    def __init__(self, workspaces: WorkspaceProvider) -> None:
        self.workspaces = workspaces

    def start(self, spec: AgentSpec) -> str:
        return "idle"

    def get_status(self, spec: AgentSpec) -> str:
        return "idle"

    def execute_task(self, spec: AgentSpec, request: TaskRequest) -> TaskOutcome:
        home = f"/workspaces/{spec.workspace_reference}"
        result = self.workspaces.execute(
            spec.workspace_reference,
            ["bash", "-lc", f"cd {home} && {request.prompt}"],
        )
        return TaskOutcome(
            status="completed" if result.exit_code == 0 else "failed",
            output=result.stdout[-8000:],
            error="" if result.exit_code == 0 else result.stdout[-2000:],
            exit_code=result.exit_code,
            detail={"provider": self.name},
        )

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.name,
            "component": "runs the prompt as a shell command inside the workspace",
            "executes_tasks": True,
            "provides_ui": False,
            "cancel": False,
            "send_input": False,
            "note": "deterministic provider for tests and operations; not a coding agent",
        }
