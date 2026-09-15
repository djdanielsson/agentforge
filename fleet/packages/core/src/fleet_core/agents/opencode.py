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

from ..config import Settings, get_settings
from ..llm import gateway_token_env
from ..workspaces.base import WorkspaceProvider
from .base import AgentError, AgentProvider, AgentSpec, TaskOutcome, TaskRequest

log = logging.getLogger(__name__)

#: Where an unprivileged agent keeps its state when the image gives it no home.
AGENT_HOME_FALLBACK = "/tmp/fleet-agent-home"


class OpenCodeProvider(AgentProvider):
    name = "opencode"

    def __init__(self, workspaces: WorkspaceProvider, settings: Settings | None = None) -> None:
        self.workspaces = workspaces
        self.settings = settings or get_settings()

    # --- helpers ----------------------------------------------------------

    def _layout(self, spec: AgentSpec):
        """Where this agent's workspace keeps its files.

        Asked of the workspace provider, not assumed: a DevPod workspace mounts
        a volume at `/workspaces/<id>` and installs its tools into it, while a
        checkout workspace *is* the repository, with its tools in the image and
        its worktrees outside itself. Hard-coding either made the second provider
        a rewrite rather than an addition.
        """
        return self.workspaces.layout(spec.workspace_reference)

    def _fleet_home(self, spec: AgentSpec) -> str:
        return self._layout(spec).fleet_home

    def _repo_path(self, spec: AgentSpec) -> str:
        return self._layout(spec).repo_path

    def _worktrees_dir(self, spec: AgentSpec) -> str:
        return self._layout(spec).worktrees_dir

    def _worktree_path(self, spec: AgentSpec, task_id: str) -> str:
        slug = spec.worktree or f"{spec.name}-{task_id}"
        return f"{self._worktrees_dir(spec)}/{slug}"

    def _shell(self, spec: AgentSpec, script: str):
        return self.workspaces.execute(spec.workspace_reference, ["bash", "-lc", script])

    def _agent_identity(self) -> str:
        """A shell prologue that resolves the unprivileged user a run should use.

        `opencode run` deadlocks when it runs as uid 0 in this devcontainer
        image. The same binary, the same config and the same directory complete
        a run in ~2s as uid 1000 and hang forever as root; the divergence is the
        whole of it (verified in the live workspace — see docs/FINDINGS.md
        §9.13). The control plane's exec always lands as root, so the run drops
        privileges itself, and falls back to root when the image has no such
        user (an image without one has no unprivileged path to offer).
        """
        user = shlex.quote(self.settings.workspace_agent_user)
        return (
            f"FLEET_AGENT_USER={user}; "
            'if id -u "$FLEET_AGENT_USER" >/dev/null 2>&1; then '
            'FLEET_AGENT_UID="$(id -u "$FLEET_AGENT_USER")"; '
            'FLEET_AGENT_GID="$(id -g "$FLEET_AGENT_USER")"; '
            'FLEET_AGENT_HOME="$(getent passwd "$FLEET_AGENT_USER" | cut -d: -f6)"; '
            f'[ -n "$FLEET_AGENT_HOME" ] || FLEET_AGENT_HOME={AGENT_HOME_FALLBACK}; '
            "mkdir -p \"$FLEET_AGENT_HOME\"; "
            'chown "$FLEET_AGENT_UID:$FLEET_AGENT_GID" "$FLEET_AGENT_HOME" 2>/dev/null || true; '
            "else FLEET_AGENT_UID=0; FLEET_AGENT_GID=0; FLEET_AGENT_HOME=/root; fi; "
            "export FLEET_AGENT_USER FLEET_AGENT_UID FLEET_AGENT_GID FLEET_AGENT_HOME; "
        )

    def _as_agent(self, script: str) -> str:
        """Run `script` as the workspace's unprivileged user, as root if it has none."""
        inner = shlex.quote(script)
        setpriv = (
            'setpriv --reuid="$FLEET_AGENT_UID" --regid="$FLEET_AGENT_GID" --init-groups '
            'env HOME="$FLEET_AGENT_HOME" bash -lc '
        )
        return (
            self._agent_identity()
            + 'if [ "$FLEET_AGENT_UID" = "0" ]; then bash -lc ' + inner + "; "
            + "else " + setpriv + inner + "; fi"
        )


    def _env_prefix(self, spec: AgentSpec, task_id: str = "") -> str:
        """The environment an agent run needs, inline for a non-login shell.

        Kubernetes exec does not read the devcontainer's `remoteEnv`, so the
        tooling path, the opencode config *and* the gateway configuration have
        to be set explicitly. `OPENCODE_CONFIG` was the one that was missed: the
        run started with no `fleet` provider defined, so `--model
        fleet/local-coder` referred to a provider opencode had never heard of
        and died with `UnknownError: Unexpected server error` before a single
        request reached the gateway. The devcontainer's `remoteEnv` names the
        same file, so both paths agree on where the config lives.

        `FLEET_TASK_ID` travels into the gateway request as a header, which is
        what lets the control plane attribute tokens to the task (SPEC §13).
        """
        home = self._fleet_home(spec)
        variables = {
            "PATH": (
                f"{self._layout(spec).bin_dir}:"
                "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
            ),
            "OPENCODE_CONFIG": f"{home}/opencode.json",
            **gateway_token_env(spec.project_id),
            "FLEET_AGENT": spec.name,
            "FLEET_TASK_ID": task_id,
            "FLEET_TASK_MODEL": spec.model,
        }
        # The config file references `{env:FLEET_API_TOKEN}` rather than holding
        # a token, so the value has to be in the run's environment. It is not
        # set when the deployment runs with no operator token.
        if self.settings.api_token:
            variables["FLEET_API_TOKEN"] = self.settings.api_token
        # HOME is deliberately not set here: the run is wrapped by `_as_agent`,
        # which sets it to the *agent user's* home. Leaving it as `/root` gave an
        # unprivileged opencode a home it could not write to.
        return " ".join(f"{k}={shlex.quote(v)}" for k, v in variables.items())

    # --- AgentProvider ----------------------------------------------------

    def start(self, spec: AgentSpec) -> str:
        """A no-op that verifies rather than pretends.

        OpenCode has no daemon to start; what `start` means here is "the
        workspace has the tools and the gateway config it needs".
        """
        bin_dir = self._layout(spec).bin_dir
        script = (
            "set -u; "
            f"test -x {bin_dir}/opencode "
            "&& echo TOOLS_OK || echo TOOLS_MISSING; "
            f"test -f {self._fleet_home(spec)}/opencode.json "
            "&& echo CONFIG_OK || echo CONFIG_MISSING; "
            f"test -x {bin_dir}/node "
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
        repo = self._repo_path(spec)
        worktrees_dir = self._worktrees_dir(spec)
        tasks_dir = self._layout(spec).tasks_dir

        # SPEC §25: each agent works in its own Git worktree so two agents on
        # one project cannot corrupt each other's tree.
        #
        # This runs as root (the control plane's exec always does) and then
        # hands the paths the agent will write to the agent's own user: the run
        # itself drops privileges, because `opencode run` deadlocks as root.
        prepare = f"""
set -u
{self._agent_identity()}
git config --global --add safe.directory '*' >/dev/null 2>&1 || true
cd {shlex.quote(repo)} 2>/dev/null || {{ echo NO_REPO; exit 3; }}
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || {{ echo NOT_A_REPO; exit 3; }}
mkdir -p {shlex.quote(worktrees_dir)} {shlex.quote(tasks_dir)}
if [ -d {shlex.quote(worktree)} ]; then
  echo WORKTREE_EXISTS
else
  git worktree add -b {shlex.quote(branch)} {shlex.quote(worktree)} 2>&1 || \
    git worktree add {shlex.quote(worktree)} 2>&1
fi
# Everything the agent touches must belong to the agent's user, including
# state a previous root-owned run left behind.
chown -R "$FLEET_AGENT_UID:$FLEET_AGENT_GID" {shlex.quote(repo)} \
  {shlex.quote(worktrees_dir)} {shlex.quote(tasks_dir)} 2>/dev/null || true
chown "$FLEET_AGENT_UID:$FLEET_AGENT_GID" {shlex.quote(home + "/credentials.env")} \
  2>/dev/null || true
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
            f"mkdir -p {shlex.quote(tasks_dir)}; "
            f"{env} "
            f"timeout {request.timeout} opencode run --model {shlex.quote(model)} "
            f"--format json --auto {shlex.quote(request.prompt)} "
            f"2>&1 | tee -a {shlex.quote(log_path)}"
        )
        result = self._shell(spec, self._as_agent(run))
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
        committed = self._shell(spec, self._as_agent(commit_script))
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
