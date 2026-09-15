"""T3 Code as a *control surface*, not as a task executor (SPEC §10, §27).

T3 Code runs inside the project workspace as a headless server and is published
on the project's own tailnet hostname, so a human can browse files, watch the
agents and drive them interactively. The fleet control plane owns project,
workspace and agent identity; T3 is one way of looking at a workspace.

`executes_tasks` is False on purpose. T3 Code drives CLIs that a human talks to;
it has no headless "run this prompt and give me a result" entry point, and
pretending otherwise would be the kind of fake integration the spec warns about.
Tasks therefore go through OpenCode; T3 is where you watch them.
"""

from __future__ import annotations

import logging
import shlex

from ..workspaces.base import WorkspaceProvider
from .base import AgentError, AgentProvider, AgentSpec, TaskOutcome, TaskRequest

log = logging.getLogger(__name__)

T3_PORT = 4096
T3_LOG = ".fleet/t3.log"
T3_PID = ".fleet/t3.pid"


class T3CodeProvider(AgentProvider):
    name = "t3code"
    executes_tasks = False
    provides_ui = True

    def __init__(self, workspaces: WorkspaceProvider) -> None:
        self.workspaces = workspaces

    def _shell(self, spec: AgentSpec, script: str):
        return self.workspaces.execute(spec.workspace_reference, ["bash", "-lc", script])

    def _env(self, spec: AgentSpec) -> str:
        home = f"/workspaces/{spec.workspace_reference}"
        path = (
            f"{home}/.fleet/tools/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        )
        return " ".join([f"PATH={shlex.quote(path)}", "HOME=/root"])

    def start(self, spec: AgentSpec) -> str:
        home = f"/workspaces/{spec.workspace_reference}"
        script = f"""
set -u
cd {shlex.quote(home)}
if [ ! -x .fleet/tools/bin/t3 ]; then echo T3_MISSING; exit 1; fi
if pgrep -f 't3 serve' >/dev/null 2>&1; then echo T3_ALREADY_RUNNING; exit 0; fi
{self._env(spec)} nohup .fleet/tools/bin/t3 serve --host 0.0.0.0 --port {T3_PORT} \
  --no-browser --mode web > {T3_LOG} 2>&1 &
echo $! > {T3_PID}
sleep 6
if pgrep -f 't3 serve' >/dev/null 2>&1; then echo T3_STARTED; \
else echo T3_FAILED; tail -n 20 {T3_LOG}; fi
"""
        result = self._shell(spec, script)
        if "T3_STARTED" in result.stdout or "T3_ALREADY_RUNNING" in result.stdout:
            return "running"
        raise AgentError(
            "T3 Code did not start in the workspace",
            detail={"probe": result.stdout[-1500:]},
        )

    def get_status(self, spec: AgentSpec) -> str:
        result = self._shell(
            spec, "pgrep -f 't3 serve' >/dev/null 2>&1 && echo RUNNING || echo STOPPED"
        )
        return "running" if "RUNNING" in result.stdout else "stopped"

    def stop(self, spec: AgentSpec) -> str:
        self._shell(spec, "pkill -f 't3 serve' >/dev/null 2>&1; echo STOPPED")
        return "stopped"

    def endpoint(self, spec: AgentSpec) -> str:
        return f"https://{spec.workspace_reference}-t3-" + _tailnet()

    def get_logs(self, spec: AgentSpec, *, task_id: str = "", tail: int = 200) -> str:
        result = self._shell(spec, f"tail -n {int(tail)} {T3_LOG} 2>/dev/null || true")
        return result.stdout

    def execute_task(self, spec: AgentSpec, request: TaskRequest) -> TaskOutcome:
        raise AgentError(
            "T3 Code is a control surface, not a headless task runner. "
            "Assign this task to an agent using the 'opencode' provider, or open the "
            "workspace's T3 URL and drive the agent there."
        )

    def cancel_task(self, spec: AgentSpec, request: TaskRequest) -> bool:
        return False

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.name,
            "component": "T3 Code headless server (`t3 serve`), started inside the workspace",
            "executes_tasks": False,
            "provides_ui": True,
            "cancel": False,
            "send_input": False,
            "reached_over": "Kubernetes Service + tailscale Ingress per project namespace",
            "note": "a human-facing control surface; tasks are executed by the opencode provider",
        }


def _tailnet() -> str:
    from ..config import get_settings

    return get_settings().tailnet_domain
