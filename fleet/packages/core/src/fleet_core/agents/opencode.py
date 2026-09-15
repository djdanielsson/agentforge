"""OpenCode as an agent provider.

OpenCode is driven headlessly with `opencode run`, and it is pointed at the
*fleet gateway* rather than at a model provider, so the agent holds a scoped
gateway token instead of an Anthropic or OpenAI key (SPEC §11).

The provider holds no Kubernetes knowledge itself: it works through the
workspace provider's `execute`, which is what keeps "runs in a DevPod
workspace" from becoming an assumption baked into the agent layer.
"""

from __future__ import annotations

import json
import logging
import shlex
import shutil

from ..llm import gateway_token_env
from ..workspaces.base import WorkspaceProvider
from .base import AgentError, AgentProvider, AgentSpec, TaskOutcome, TaskRequest

log = logging.getLogger(__name__)


class OpenCodeProvider(AgentProvider):
    name = "opencode"

    def __init__(self, workspaces: WorkspaceProvider) -> None:
        self.workspaces = workspaces

    # --- helpers ----------------------------------------------------------

    def _fleet_home(self, spec: AgentSpec) -> str:
        return f"/workspaces/{spec.workspace_reference}/.fleet"

    def _worktree_path(self, spec: AgentSpec, task_id: str) -> str:
        home = self._fleet_home(spec)
        slug = spec.worktree or f"{spec.name}-{task_id}"
        return f"{home}/worktrees/{slug}"

    def _shell(self, spec: AgentSpec, script: str):
        return self.workspaces.execute(spec.workspace_reference, ["bash", "-lc", script])

    def _env_prefix(self, spec: AgentSpec, task_id: str = "") -> str:
        """The environment an agent run needs, inline for a non-login shell.

        Kubernetes exec does not read the devcontainer's `remoteEnv`, so the
        tooling path and the gateway configuration have to be set explicitly.
        `FLEET_TASK_ID` travels into the gateway request as a header, which is
        what lets the control plane attribute tokens to the task (SPEC §13).
        """
        home = self._fleet_home(spec)
        variables = {
            "PATH": (
                f"{home}/tools/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            ),
            "HOME": "/root",
            **gateway_token_env(spec.project_id),
            "FLEET_AGENT": spec.name,
            "FLEET_TASK_ID": task_id,
            "FLEET_TASK_MODEL": spec.model,
        }
        return " ".join(f"{k}={shlex.quote(v)}" for k, v in variables.items())

    # --- AgentProvider ----------------------------------------------------

    def start(self, spec: AgentSpec) -> str:
        """A no-op that verifies rather than pretends.

        OpenCode has no daemon to start; what `start` means here is "the
        workspace has the tools and the gateway config it needs".
        """
        script = (
            "set -u; "
            f"test -x {self._fleet_home(spec)}/tools/bin/opencode "
            "&& echo TOOLS_OK || echo TOOLS_MISSING; "
            f"test -f {self._fleet_home(spec)}/opencode.json "
            "&& echo CONFIG_OK || echo CONFIG_MISSING; "
            f"test -x {self._fleet_home(spec)}/tools/bin/node "
            "&& echo NODE_OK || echo NODE_MISSING"
        )
        result = self._shell(spec, script)
        output = result.stdout
        if "TOOLS_OK" not in output:
            raise AgentError(
                "opencode is not installed in the workspace",
                detail={"probe": output[:500]},
            )
        return "idle"

    def get_status(self, spec: AgentSpec) -> str:
        result = self._shell(
            spec,
            f"pgrep -f {shlex.quote('opencode run')} >/dev/null && echo RUNNING || echo IDLE",
        )
        return "running" if "RUNNING" in result.stdout else "idle"

    def execute_task(self, spec: AgentSpec, request: TaskRequest) -> TaskOutcome:
        worktree = self._worktree_path(spec, request.task_id)
        branch = request.branch or f"fleet/{spec.name}-{request.task_id}"
        home = self._fleet_home(spec)

        # SPEC §25: each agent works in its own Git worktree so two agents on
        # one project cannot corrupt each other's tree.
        prepare = f"""
set -u
git config --global --add safe.directory '*' >/dev/null 2>&1 || true
cd {shlex.quote(home + "/repo")} 2>/dev/null || {{ echo NO_REPO; exit 3; }}
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {{ echo NOT_A_REPO; exit 3; }}
mkdir -p {shlex.quote(home + "/worktrees")}
if [ -d {shlex.quote(worktree)} ]; then
  echo WORKTREE_EXISTS
else
  git worktree add -b {shlex.quote(branch)} {shlex.quote(worktree)} 2>&1 || \
    git worktree add {shlex.quote(worktree)} 2>&1
fi
echo WORKTREE_READY {shlex.quote(worktree)}
"""
        prepared = self._shell(spec, prepare)
        if "WORKTREE_READY" not in prepared.stdout and "WORKTREE_EXISTS" not in prepared.stdout:
            return TaskOutcome(
                status="failed",
                output=prepared.stdout[-4000:],
                error="could not prepare a Git worktree",
                exit_code=prepared.exit_code or 1,
                detail={"stage": "worktree"},
            )

        env = self._env_prefix(spec, request.task_id)
        model = f"fleet/{spec.model}"
        credentials = f"{home}/credentials.env"
        log_path = f"{home}/tasks/{request.task_id}.log"
        # `set -o pipefail` before the pipeline: `tee` exists so the run's own
        # output is readable afterwards (`GET /agents/{id}/logs`), and without
        # pipefail the pipeline would report tee's status instead of opencode's.
        run = (
            f"cd {shlex.quote(worktree)} || {{ echo NO_WORKTREE; exit 3; }}; "
            "set -o pipefail; "
            # The project's credentials are state in the workspace volume, not
            # in a command line (SPEC §16).
            f"set -a; [ -f {shlex.quote(credentials)} ] && . {shlex.quote(credentials)}; set +a; "
            f"mkdir -p {shlex.quote(home + '/tasks')}; "
            f"{env} "
            f"timeout {request.timeout} opencode run --model {shlex.quote(model)} "
            f"--format json --auto {shlex.quote(request.prompt)} "
            f"2>&1 | tee -a {shlex.quote(log_path)}"
        )
        result = self._shell(spec, run)
        output = result.stdout

        # `--format json` emits one JSON event per line; the last assistant text
        # is the agent's answer, and an `error` event is a failure whatever the
        # process said its exit status was.
        summary = _last_assistant_text(output)
        run_error = _error_from_events(output)

        commit_script = f"""
set -u
cd {shlex.quote(worktree)}
git config user.email fleet@localhost >/dev/null 2>&1 || true
git config user.name fleet >/dev/null 2>&1 || true
git add -A >/dev/null 2>&1 || true
changed=$(git status --porcelain | wc -l)
commit=$(git rev-parse HEAD)
if [ "$changed" -gt 0 ]; then
  git commit -qm "fleet: {request.task_id}" >/dev/null 2>&1 && commit=$(git rev-parse HEAD)
fi
echo "CHANGED=$changed"
echo "COMMIT=$commit"
echo "BRANCH=$(git rev-parse --abbrev-ref HEAD)"
git diff --name-only HEAD~1 2>/dev/null | head -20 || true
"""
        committed = self._shell(spec, commit_script)
        detail: dict[str, object] = {"stage": "run"}
        commit_sha = ""
        branch_name = branch
        files: list[str] = []
        for line in committed.stdout.splitlines():
            if line.startswith("COMMIT="):
                commit_sha = line.split("=", 1)[1].strip()
            elif line.startswith("BRANCH="):
                branch_name = line.split("=", 1)[1].strip()
            elif line.startswith("CHANGED="):
                detail["changed_files"] = line.split("=", 1)[1].strip()
            elif line and "/" in line and not line.startswith(" "):
                files.append(line.strip())

        status = "completed" if result.exit_code == 0 and not run_error else "failed"
        error = ""
        if status == "failed":
            error = run_error or _tail(output, 2000)
        detail["exit_code"] = result.exit_code
        return TaskOutcome(
            status=status,
            output=(summary or output)[-8000:],
            error=error,
            exit_code=result.exit_code,
            git_branch=branch_name,
            git_commit=commit_sha,
            files_changed=files[:20],
            detail=detail,
        )

    def cancel_task(self, spec: AgentSpec, request: TaskRequest) -> bool:
        result = self._shell(spec, "pkill -f 'opencode run' && echo KILLED || echo NOTHING")
        return "KILLED" in result.stdout

    def get_logs(self, spec: AgentSpec, *, task_id: str = "", tail: int = 200) -> str:
        path = f"{self._fleet_home(spec)}/tasks/{task_id}.log"
        result = self._shell(spec, f"tail -n {int(tail)} {shlex.quote(path)} 2>/dev/null || true")
        return result.stdout

    def send_input(self, spec: AgentSpec, text: str) -> bool:
        """Headless `opencode run` reads no stdin; say so rather than pretend."""
        return False

    def capabilities(self) -> dict[str, object]:
        return {
            "provider": self.name,
            "component": "opencode-ai CLI, headless (`opencode run`)",
            "executes_tasks": True,
            "provides_ui": False,
            "cancel": True,
            "send_input": False,
            "llm": "fleet gateway via an OpenAI-compatible base URL",
            "worktrees": True,
        }


def _last_assistant_text(raw: str) -> str:
    """Pull the final assistant message out of `--format json` output."""
    texts: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        for key in ("text", "content", "message"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                texts.append(value)
            elif isinstance(value, dict) and isinstance(value.get("text"), str):
                texts.append(value["text"])
        part = event.get("part")
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            texts.append(part["text"])
    if texts:
        return texts[-1]
    return raw.strip()


def _error_from_events(raw: str) -> str:
    """The first `error` event in `--format json` output, as a readable string.

    OpenCode reports a failed model call as an event, and the worktree can be
    untouched with a zero exit status: the only reliable signal that the run
    failed is the event stream. Observed in a live deployment, where every call
    was refused at connect time — the task was recorded as `completed` and the
    operator saw nothing wrong.
    """
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "error":
            continue
        error = event.get("error")
        if isinstance(error, dict):
            name = str(error.get("name") or "Error")
            data = error.get("data")
            message = ""
            if isinstance(data, dict):
                message = str(data.get("message") or data.get("error") or "")
            elif isinstance(data, str):
                message = data
            return f"opencode {name}: {message}".strip()
        if isinstance(error, str):
            return f"opencode: {error}"
        return "opencode reported an error"
    return ""


def _tail(text: str, limit: int) -> str:
    return text[-limit:] if len(text) > limit else text


def opencode_available() -> bool:
    return shutil.which("opencode") is not None
