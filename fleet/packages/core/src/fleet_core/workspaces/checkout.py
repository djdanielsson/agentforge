"""Checkout workspaces: a project as a directory in one shared T3 environment.

The DevPod provider gives every project its own namespace, pod and volume. This
provider is the other shape (T3-INTEGRATION §A): **one** T3 Code environment
owns every project as a *checkout* — a directory under a shared volume — and the
control plane provisions the directory and registers it with T3.

What is the same as DevPod: the control plane still drives everything through
`WorkspaceProvider`, tasks still run in their own git worktree, credentials still
arrive as a 0600 env file written over stdin.

What is different, and is the point of the trade-off: there is no per-project
isolation boundary. A checkout workspace is a directory, not a namespace, so the
namespace/PVC/NetworkPolicy guarantees of SPEC §18 do not apply to it. The
provider says so in `capabilities()` rather than letting the API imply
otherwise.

Everything the provider runs, it runs inside the T3 pod. That is a deliberate
sink: nothing here reads or writes the shared volume from outside, so permissions
are whatever the pod's user has and there is no second way to reach the files.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import time
from collections.abc import Callable

from ..config import Settings, get_settings
from . import kubernetes_common
from .base import (
    ExecResult,
    ProviderError,
    WorkspaceLayout,
    WorkspaceProvider,
    WorkspaceSpec,
    WorkspaceState,
)

log = logging.getLogger(__name__)

#: How long `prepare` waits for the shared T3 environment to exist.
ENVIRONMENT_TIMEOUT = 300

#: A reference is a directory name on a shared volume. It is generated from a
#: project name by `service.slugify`, but it also arrives here from stored rows,
#: and this provider is the last thing between a value and `rm -rf`. Nothing
#: outside `[a-z0-9-]` is a project directory name.
REFERENCE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

Runner = Callable[..., ExecResult]


class CheckoutWorkspaceProvider(WorkspaceProvider):
    name = "checkout"
    #: A checkout's workspace is a directory in a shared environment, so there is
    #: no namespace to copy a project's Secret into. The credentials are read from
    #: the control plane's own namespace and written into the checkout over the
    #: exec stream instead.
    credentials_in_namespace = False

    def __init__(self, settings: Settings | None = None, runner: Runner | None = None) -> None:
        self.settings = settings or get_settings()
        #: Injected by tests, and by anything that wants to reach the
        #: environment without a cluster handle. Production leaves it None and
        #: goes through the API server.
        self._runner = runner
        self._cluster = None

    # --- plumbing ---------------------------------------------------------

    @property
    def cluster(self):
        if self._cluster is None:
            self._cluster = kubernetes_common.get_cluster()
        return self._cluster

    def available(self) -> tuple[bool, str]:
        """Is the shared T3 environment there?

        Asked of the cluster, not assumed. The registry falls back when a
        provider says no, and a fallback that was not detected is a project that
        silently lands somewhere else.
        """
        try:
            pod = self.pod()
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            return False, f"cannot reach the cluster: {type(exc).__name__}: {exc}"
        if not pod:
            return False, (
                f"no T3 environment pod in {self.settings.t3_namespace} "
                f"matching {self.settings.t3_pod_selector!r}"
            )
        return True, pod

    def pod(self) -> str:
        return self.cluster.find_pod(self.settings.t3_namespace, self.settings.t3_pod_selector)

    def pod_ready(self) -> bool:
        pod = self.pod()
        if not pod:
            return False
        return bool(self.cluster.pod_ready(self.settings.t3_namespace, pod))

    # --- the interface ----------------------------------------------------

    def layout(self, reference: str) -> WorkspaceLayout:
        """A checkout workspace *is* the project directory.

        Tools are in the image at `/usr/local/bin` (node, t3, opencode), not in
        the workspace, because the workspace is a git checkout and nothing else.
        """
        root = f"{self.settings.t3_projects_dir.rstrip('/')}/{reference}"
        return WorkspaceLayout(root=root, bin_dir="/usr/local/bin")

    def prepare(self, spec: WorkspaceSpec) -> None:
        """Wait for the shared environment. Nothing is created here.

        A checkout workspace has no boundary of its own to build, so `prepare`
        is the honest no-op for this provider — except that the environment has
        to be *there*, and saying so now gives a real error instead of a stream
        of failed execs.
        """
        deadline = time.monotonic() + ENVIRONMENT_TIMEOUT
        last = ""
        while time.monotonic() < deadline:
            ok, detail = self.available()
            if ok:
                return
            last = detail
            time.sleep(5)
        raise ProviderError(
            f"the T3 environment is not ready: {last}",
            detail={
                "namespace": self.settings.t3_namespace,
                "selector": self.settings.t3_pod_selector,
            },
        )

    def create(self, spec: WorkspaceSpec) -> WorkspaceState:
        """Make the project a checkout in the environment, and register it.

        `t3 project add` is what makes the directory a project *in T3*: a
        directory on the volume is invisible to T3 on its own (verified against
        v0.0.40 — see docs/T3-INTEGRATION.md). The order matters: the directory
        has to be a git checkout first, because T3 reads the repository to name
        the checkout's branch.
        """
        slug = self._checked(spec.reference)
        self.prepare(spec)
        layout = self.layout(slug)

        self._write_credentials(spec, layout)
        self._write_opencode_config(spec, layout)
        result = self._run(["/bin/bash", "-lc", self._create_script(spec, slug, layout)])
        if result.exit_code != 0:
            return WorkspaceState(
                reference=slug,
                provider=self.name,
                status="failed",
                error=(result.stderr or result.stdout)[-2000:],
                detail={"stage": "create"},
            )

        state = self.status(slug)
        output = result.stdout or ""
        state.detail["create_output"] = output[-1500:]
        state.detail["project_id"] = _marker(output, "T3_PROJECT_ID")
        state.detail["branch"] = _marker(output, "BRANCH")
        # "Not ready yet" and "it failed" are different states, and only one of
        # them is worth waiting for. git's own message is the diagnosis.
        failure = ""
        if "CLONE_FAILED" in output:
            failure = _marker(output, "CLONE_ERROR") or "git clone failed"
        elif "T3_REGISTER_FAILED" in output:
            failure = "t3 project add did not register the checkout"
        if failure:
            state.status = "failed"
            state.error = failure
        elif not state.ready:
            state.status = "provisioning"
        return state

    def start(self, reference: str) -> WorkspaceState:
        """Bring a checkout back.

        The shared environment is not this workspace's to stop or start, so
        "start" means the project's directory is present and registered with T3
        — which is exactly what makes it usable again after a volume was pruned
        or a registration was removed.
        """
        slug = self._checked(reference)
        self.prepare(WorkspaceSpec(project_id="", project_name=slug, reference=slug))
        result = self._run(["/bin/bash", "-lc", self._register_script(slug)])
        if result.exit_code != 0:
            raise ProviderError(
                f"could not register {slug} with T3",
                detail={"output": (result.stdout or result.stderr)[-1000:]},
            )
        return self.status(slug)

    def stop(self, reference: str) -> None:
        raise ProviderError(
            "a checkout workspace is a directory in the shared T3 environment; "
            "there is no per-project pod to stop. Stop the environment "
            f"({self.settings.t3_namespace}/t3) to take every checkout offline.",
            detail={"reference": reference, "provider": self.name},
        )

    def destroy(self, reference: str) -> None:
        """Forget the project in T3 and delete its checkout.

        The directory goes; the credentials file goes with it. Nothing else is
        touched: the volume, the environment and every other project stay.
        """
        slug = self._checked(reference)
        result = self._run(
            [
                "/bin/bash",
                "-lc",
                # `t3 project remove` first: removing a directory T3 still has a
                # project row for leaves a project whose checkouts are gone.
                "set -u; "
                f"t3 project remove {shlex.quote(slug)} --force "
                f'--base-dir {shlex.quote(self.settings.t3_home)} 2>&1 || true; '
                f"rm -rf -- {shlex.quote(self.layout(slug).root)}; "
                "echo DESTROYED",
            ]
        )
        if "DESTROYED" not in result.stdout:
            raise ProviderError(
                f"could not delete the checkout for {slug}",
                detail={"output": (result.stdout or result.stderr)[-1000:]},
            )

    def status(self, reference: str) -> WorkspaceState:
        slug = self._checked(reference)
        state = WorkspaceState(reference=slug, provider=self.name)
        try:
            pod = self.pod()
        except Exception as exc:  # noqa: BLE001 - a status, not a crash
            state.status = "unknown"
            state.error = f"{type(exc).__name__}: {exc}"
            return state
        if not pod:
            state.status = "stopped"
            state.detail = {"reason": "the shared T3 environment is not running"}
            return state

        state.pod_name = pod
        state.service_name = self.settings.t3_service
        state.url = (
            f"http://{self.settings.t3_service}.{self.settings.t3_namespace}"
            ".svc.cluster.local:5733"
        )
        state.t3_url = self.settings.t3_url

        ready_pod = bool(self.cluster.pod_ready(self.settings.t3_namespace, pod))
        root = self.layout(slug).root
        probe = self._run(
            [
                "/bin/bash",
                "-lc",
                f"test -d {shlex.quote(root + '/.git')} && echo CHECKOUT_OK "
                f"|| echo CHECKOUT_MISSING",
            ]
        )
        checkout = "CHECKOUT_OK" in (probe.stdout or "")
        state.ready = ready_pod and checkout
        if state.ready:
            state.status = "ready"
        elif not ready_pod:
            state.status = "provisioning"
            state.error = ""
        else:
            state.status = "pending"
        state.detail = {
            "checkout": root,
            "environment": f"{self.settings.t3_namespace}/{pod}",
            "shared": True,
            "t3_project_registered": checkout,
        }
        return state

    def execute(
        self,
        reference: str,
        command: list[str],
        *,
        container: str | None = None,
        stdin_data: str | None = None,
    ) -> ExecResult:
        """Run a command in the shared environment.

        There is no per-project container to enter; the boundary is the working
        directory the command chooses, which is why the agent layer passes
        absolute paths under the layout.
        """
        self._checked(reference)
        try:
            return self._run(command, stdin_data=stdin_data, container=container)
        except Exception as exc:  # noqa: BLE001 - a failed command, not a crash
            return ExecResult(" ".join(command), 1, stderr=f"{type(exc).__name__}: {exc}")

    def get_logs(self, reference: str, *, tail: int = 200) -> str:
        """The environment's log, labelled with what it is.

        A checkout has no log of its own. Returning the environment's log
        unlabelled would read as the project's, so the header says otherwise.
        """
        self._checked(reference)
        try:
            pod = self.pod()
        except Exception as exc:  # noqa: BLE001
            return f"<no environment: {type(exc).__name__}: {exc}>"
        if not pod:
            return "<no T3 environment pod>"
        body = self.cluster.pod_logs(
            self.settings.t3_namespace, pod, container=self.settings.t3_container, tail=tail
        )
        return f"<shared T3 environment {pod}; not specific to {reference}>\n{body}"

    def capabilities(self) -> dict[str, object]:
        try:
            ok, detail = self.available()
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        return {
            "provider": self.name,
            "component": "one T3 Code environment; projects are checkouts on a shared volume",
            "available": ok,
            "pod": detail if ok else "",
            "reason": "" if ok else detail,
            "isolation": (
                "none of its own: projects are directories in one environment "
                "(no namespace, no NetworkPolicy, no per-project identity)"
            ),
            "create": True,
            "start_stop": False,
            "exec": True,
            "persistent_volumes": True,
            "network_policy": False,
            "t3": True,
            "projects_dir": self.settings.t3_projects_dir,
            "notes": (
                "choose this when one T3 UI over many projects matters more than "
                "per-project isolation; choose devpod when it is the other way round"
            ),
        }

    # --- scripts ----------------------------------------------------------

    def _create_script(self, spec: WorkspaceSpec, slug: str, layout: WorkspaceLayout) -> str:
        """Clone or init the checkout, then hand it to T3, printing markers.

        The markers exist so the control plane records what *happened* rather
        than what was intended — silence is how a task that never reached a
        model got recorded as complete (FINDINGS §9.14), and how a checkout that
        never cloned reads as one that is still working.

        A clone failure keeps git's own message. A private repository with no
        project credential fails exactly here, and "the workspace is
        provisioning" is not a diagnosis an operator can act on.
        """
        root = shlex.quote(layout.root)
        home = shlex.quote(layout.fleet_home)
        credentials = shlex.quote(f"{layout.fleet_home}/credentials.env")
        url = shlex.quote(spec.repository_url)
        branch = shlex.quote(spec.repository_branch or "main")
        agent_user = shlex.quote(self.settings.workspace_agent_user)
        # The clone lands in a scratch directory and is copied in. `git clone`
        # refuses a non-empty destination, and this directory is already
        # non-empty by the time we get here: `.fleet/` has to exist *before* the
        # clone, because the clone is authenticated with a credential that lives
        # in it.
        return f"""set -u
export PATH=/usr/local/bin:$PATH
mkdir -p {shlex.quote(self.settings.t3_projects_dir)} {home}
if [ -d {root}/.git ]; then
  echo CHECKOUT_EXISTS
elif [ -n {url} ]; then
  repo_url={url}
  [ -f {credentials} ] && . {credentials}
  if [ -n "${{GITHUB_TOKEN:-}}" ]; then
    repo_url=$(printf '%s' "$repo_url" | \\
      sed -E 's#^https://#https://x-access-token:${{GITHUB_TOKEN}}@#')
  fi
  scratch=$(mktemp -d)
  if clone_out=$(git clone --branch {branch} "$repo_url" "$scratch/repo" 2>&1); then
    mkdir -p {root}
    cp -a "$scratch/repo/." {root}/
    echo CLONE_OK
  else
    # `sed` masks the token so a retry's log cannot leak it.
    echo "CLONE_ERROR=$(printf '%s' "$clone_out" | \\
      sed -E 's#x-access-token:[^@]*@#x-access-token:***@#g' | tail -n 1)"
    echo CLONE_FAILED
  fi
  rm -rf "$scratch"
  git -C {root} remote set-url origin {url} 2>/dev/null || true
else
  git init -q {root}
  git -C {root} -c user.email=fleet@localhost -c user.name=fleet \\
    commit -q --allow-empty -m 'fleet: empty checkout'
  echo CLONE_EMPTY
fi
if [ -d {root}/.git/info ]; then
  # Fleet's own files in the checkout stay out of `git status` — locally, never
  # committed, so a checkout does not look dirty to T3 or to an agent.
  for entry in opencode.json .fleet/; do
    grep -qxF "$entry" {root}/.git/info/exclude 2>/dev/null || \\
      echo "$entry" >> {root}/.git/info/exclude
  done
fi
if id -u {agent_user} >/dev/null 2>&1; then
  chown -R {agent_user}:{agent_user} {root} 2>/dev/null || true
fi
git config --global --add safe.directory '*' >/dev/null 2>&1 || true
if [ -d {root}/.git ]; then
  git -C {root} config user.email fleet@localhost
  git -C {root} config user.name fleet
  echo "BRANCH=$(git -C {root} rev-parse --abbrev-ref HEAD 2>/dev/null || echo none)"
else
  echo BRANCH=none
fi
{self._register_script(slug)}"""

    def _register_script(self, slug: str) -> str:
        """Make the directory a project in T3, and prove it took.

        `t3 project add` is idempotent in effect for our purposes: it prints the
        existing project when the workspace root is already registered, and the
        new project's id when it is not. Both are success.
        """
        root = f"{self.settings.t3_projects_dir.rstrip('/')}/{slug}"
        return "; ".join(
            [
                "set -u",
                "export PATH=/usr/local/bin:$PATH",
                f"out=$(t3 project add {shlex.quote(root)} "
                f"--base-dir {shlex.quote(self.settings.t3_home)} 2>&1)",
                'status=$?',
                'if [ "$status" = "0" ]; then echo "$out"; '
                "echo T3_REGISTERED; else echo \"$out\"; echo T3_REGISTER_FAILED; fi"
            ]
        )

    # --- files ------------------------------------------------------------

    def _write_credentials(self, spec: WorkspaceSpec, layout: WorkspaceLayout) -> None:
        """The project's credentials, into the checkout, over stdin.

        Same mechanism as the DevPod provider and for the same reason: the
        Kubernetes exec API records the command it is given, so a value in
        `argv` is a value in the API server's log. `head -c <bytes>` and not
        `cat`, because the exec stream has no EOF signal.
        """
        if not spec.secrets:
            return
        from .. import secrets

        by_key = {ref.key: ref for ref in spec.secrets}
        values: dict[str, str] = {}
        for secret_name in {ref.secret_name for ref in spec.secrets}:
            try:
                # The control plane's own namespace: the project's Secret is
                # never copied anywhere with a checkout workspace, because there
                # is no per-project namespace to copy it to.
                items = secrets.secret_values(self.settings.namespace, secret_name).items()
            except Exception as exc:  # noqa: BLE001 - a missing secret is not fatal here
                log.warning("cannot read %s for %s: %s", secret_name, spec.reference, exc)
                continue
            for key, value in items:
                ref = by_key.get(key)
                env_var = (ref.env_var if ref else "") or f"{key.upper()}_TOKEN"
                if "\n" in value:
                    log.warning("skipping credential %s: multi-line value", key)
                    continue
                values[env_var] = value
        if not values:
            return
        path = f"{layout.fleet_home}/credentials.env"
        payload = "".join(f"{k}={v}\n" for k, v in values.items())
        result = self._run(
            [
                "/bin/bash",
                "-lc",
                f"umask 077; mkdir -p {shlex.quote(layout.fleet_home)}; "
                f"head -c {len(payload.encode())} > {shlex.quote(path)}; "
                f"chmod 600 {shlex.quote(path)}; {self._chown_to_agent(path)}",
            ],
            stdin_data=payload,
        )
        if result.exit_code != 0:
            log.warning("writing credentials into %s failed: %s", path, result.stderr[:300])

    def _write_opencode_config(self, spec: WorkspaceSpec, layout: WorkspaceLayout) -> None:
        """The opencode config for this project, in two places.

        `.fleet/opencode.json` is what a fleet-driven run points `OPENCODE_CONFIG`
        at. The copy at the checkout root is what a *T3-driven* session in that
        directory picks up: opencode reads a project config from the working
        directory upward, so a session opened on `/projects/<name>` gets the
        fleet gateway and the fleet tools without anyone configuring it. The
        copy is added to `.git/info/exclude` (local, never committed) so it does
        not show up as an untracked file in the checkout.

        Written over stdin for the same reason credentials are: a document in
        `argv` is a document in the API server's log.
        """
        payload = json.dumps(opencode_config(spec, self.settings), indent=2)
        root_config = f"{layout.root}/opencode.json"
        wrote = self._run(
            [
                "/bin/bash",
                "-lc",
                f"umask 022; mkdir -p {shlex.quote(layout.fleet_home)}; "
                f"head -c {len(payload.encode())} > "
                f"{shlex.quote(layout.config_path)}; "
                f"chmod 644 {shlex.quote(layout.config_path)}; "
                f"cp {shlex.quote(layout.config_path)} {shlex.quote(root_config)}; "
                f"{self._chown_to_agent(layout.config_path)}; "
                f"{self._chown_to_agent(root_config)}",
            ],
            stdin_data=payload,
        )
        if wrote.exit_code != 0:
            log.warning("writing %s failed: %s", layout.config_path, wrote.stderr[:300])

    def _chown_to_agent(self, path: str) -> str:
        user = shlex.quote(self.settings.workspace_agent_user)
        return (
            f'if id -u {user} >/dev/null 2>&1; then '
            f"chown {user}:{user} {shlex.quote(path)} 2>/dev/null || true; fi"
        )

    # --- transport --------------------------------------------------------

    def _run(
        self,
        command: list[str],
        *,
        stdin_data: str | None = None,
        container: str | None = None,
    ) -> ExecResult:
        if self._runner is not None:
            return self._runner(command, stdin_data=stdin_data, container=container)
        pod = self.pod()
        if not pod:
            return ExecResult(
                " ".join(command), 127, stderr="the shared T3 environment is not running"
            )
        outcome = self.cluster.exec(
            self.settings.t3_namespace,
            pod,
            command,
            container=container or self.settings.t3_container,
            stdin_data=stdin_data,
        )
        return ExecResult(
            " ".join(command), outcome.exit_code, stdout=outcome.output, stderr=""
        )

    def _checked(self, reference: str) -> str:
        """A reference is a directory name, and only a directory name."""
        candidate = (reference or "").strip()
        if not REFERENCE_PATTERN.match(candidate):
            raise ProviderError(
                f"{reference!r} is not a valid checkout reference: a project directory "
                "name may contain only a-z, 0-9 and '-', and must not start with '-'",
                detail={"reference": reference},
            )
        return candidate


def opencode_config(spec: WorkspaceSpec, settings: Settings | None = None) -> dict:
    """The opencode config for a fleet-run agent in a checkout workspace.

    Two things it must carry: the gateway (so the agent has a model and holds no
    vendor key) and the fleet MCP server (so it can act on the fleet, not just
    answer). It is written per project rather than baked into the image because
    the token in it is project-scoped.
    """
    settings = settings or get_settings()
    model = spec.environment.get("FLEET_LLM_MODEL", settings.llm_default_model)
    models = spec.environment.get("FLEET_LLM_MODELS", "").split(",") or [model]
    base_url = spec.environment.get("FLEET_LLM_BASE_URL", "")
    server: dict = {
        "type": "remote",
        "url": settings.mcp_endpoint,
        "enabled": True,
    }
    if settings.api_token:
        # A reference, not a value. Every project in a shared environment runs
        # as the same user, so a token written to this file is a token readable
        # by every other project's agent on the same volume.
        server["headers"] = {"Authorization": "Bearer {env:FLEET_API_TOKEN}"}
    return {
        "$schema": "https://opencode.ai/config.json",
        "autoupdate": False,
        "model": f"fleet/{model}",
        "mcp": {"fleet": server},
        "provider": {
            "fleet": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Fleet Gateway",
                "options": {
                    "baseURL": base_url,
                    # Same reason as the header above: the run is handed the
                    # project-scoped token in its environment (see
                    # `agents.opencode._env_prefix`), and it is never on disk.
                    "apiKey": "{env:FLEET_LLM_TOKEN}",
                },
                "models": {name: {"name": name} for name in models if name},
            }
        },
    }


def _marker(output: str, key: str) -> str:
    """The value of a `KEY=value` line in a script's output, or ""."""
    for line in (output or "").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return ""
