"""Checkout workspaces: one T3 environment, projects as directories.

The interesting properties are the ones that differ from DevPod, so that is what
is tested here: the reference is a directory name and nothing else, the layout
is the project directory rather than a mounted volume, `t3 project add` is what
makes a directory a project, and the provider says plainly what it cannot do.
"""

from __future__ import annotations

import json
import re

import pytest
from fleet_core.agents.base import AgentSpec
from fleet_core.agents.opencode import OpenCodeProvider
from fleet_core.config import get_settings
from fleet_core.workspaces.base import ProviderError, WorkspaceSpec
from fleet_core.workspaces.checkout import CheckoutWorkspaceProvider, opencode_config


class FakeT3Environment:
    """A shared T3 environment, in memory.

    It answers the commands the provider sends the way a shell in the pod would,
    and records them, so a test can assert on what the provider *asked for*
    rather than on a mock's return value.
    """

    def __init__(self) -> None:
        self.commands: list[str] = []
        self.writes: list[tuple[str, str]] = []
        self.checkouts: set[str] = set()
        self.registered: set[str] = set()
        self.clone_fails = False
        self.register_errors = False

    def _slug(self, command: str) -> str:
        match = re.search(r"/projects/([a-z0-9][a-z0-9-]*)", command)
        return match.group(1) if match else ""

    def __call__(self, command, *, stdin_data=None, container=None):  # noqa: ARG002
        from fleet_core.workspaces.base import ExecResult

        script = " ".join(command)
        self.commands.append(script)
        if stdin_data is not None:
            self.writes.append((script, stdin_data))
        slug = self._slug(script)

        if "TOOLS_OK" in script:
            return ExecResult(script, 0, stdout="TOOLS_OK\nCONFIG_OK\nNODE_OK\n")
        if "CHECKOUT_OK" in script:
            output = "CHECKOUT_OK" if slug in self.checkouts else "CHECKOUT_MISSING"
            return ExecResult(script, 0, stdout=output)
        if "DESTROYED" in script:
            self.checkouts.discard(slug)
            self.registered.discard(slug)
            return ExecResult(script, 0, stdout="DESTROYED\n")

        # The create script does both jobs in one shell, so answer both.
        output = ""
        if "git clone" in script or "git init" in script:
            if self.clone_fails:
                # A private repository with no project credential, verbatim.
                output += (
                    "CLONE_ERROR=fatal: could not read Username for "
                    "'https://github.com': No such device or address\nCLONE_FAILED\n"
                )
            else:
                self.checkouts.add(slug)
                output += "CLONE_OK\n"
        if "t3 project add" in script:
            # A real `t3 project add` needs the directory to exist.
            if self.register_errors:
                output += "Error: something went wrong\nT3_REGISTER_ERROR=Error: something went wrong\nT3_REGISTER_FAILED\n"
            elif slug in self.checkouts:
                self.registered.add(slug)
                output += f"Added project 54e5a6be (demo) at /projects/{slug}.\nT3_REGISTERED\n"
            else:
                output += f"Error: no such directory /projects/{slug}\nT3_REGISTER_FAILED\n"
        return ExecResult(script, 0, stdout=output or "ok\n")


def _spec(reference="demo", **overrides):
    values = {
        "project_id": "prj_demo",
        "project_name": "demo",
        "reference": reference,
        "repository_url": "https://github.com/example/demo",
        "repository_branch": "main",
        "environment": {
            "FLEET_LLM_TOKEN": "project-token-value",
            "FLEET_LLM_BASE_URL": "http://fleet-api.fleet.svc.cluster.local:8000/llm/v1",
            "FLEET_LLM_MODEL": "local-coder",
            "FLEET_LLM_MODELS": "local-coder,fast",
        },
    }
    values.update(overrides)
    return WorkspaceSpec(**values)


def _provider(environment=None):
    return CheckoutWorkspaceProvider(get_settings(), runner=environment or FakeT3Environment())


def test_a_reference_is_a_directory_name_and_only_that(no_real_cluster):  # noqa: ARG001
    """The provider is the last thing between a stored value and `rm -rf`."""
    provider = _provider()
    for bad in ("../../etc", "Demo", "-demo", "", "de mo", "a/b", "fleet-demo;rm -rf /"):
        with pytest.raises(ProviderError):
            provider.status(bad)
        with pytest.raises(ProviderError):
            provider.destroy(bad)


def test_the_layout_is_the_project_directory_not_a_mounted_volume(no_real_cluster):  # noqa: ARG001
    layout = _provider().layout("demo")
    assert layout.root == "/projects/demo"
    assert layout.fleet_home == "/projects/demo/.fleet"
    # The checkout *is* the repository.
    assert layout.repo_path == "/projects/demo"
    # The tools are in the image; there is no per-workspace toolchain.
    assert layout.bin_dir == "/usr/local/bin"
    # Worktrees are outside the checkout: nested inside it they would show in
    # the checkout's own `git status`, and `git add -A` there stages another
    # agent's worktree as an embedded repository.
    assert layout.worktrees_dir == "/projects/.fleet/demo/worktrees"
    assert not layout.worktrees_dir.startswith(layout.repo_path + "/")


def test_create_clones_the_checkout_and_registers_it_with_t3(no_real_cluster):  # noqa: ARG001
    environment = FakeT3Environment()
    provider = _provider(environment)
    state = provider.create(_spec())

    assert state.ready
    assert state.status == "ready"
    assert "demo" in environment.checkouts
    assert "demo" in environment.registered
    joined = "\n".join(environment.commands)
    assert "git clone --branch main" in joined
    # Registering the directory is what makes it a project in T3; a directory on
    # the volume is invisible to T3 on its own.
    assert "t3 project add /projects/demo" in joined
    assert state.detail["t3_project_registered"] is True
    # No namespace, no service, no per-project pod: the environment is shared.
    assert state.detail["shared"] is True


def test_create_fails_loudly_when_the_checkout_cannot_be_made(no_real_cluster):  # noqa: ARG001
    environment = FakeT3Environment()
    environment.clone_fails = True
    provider = _provider(environment)
    state = provider.create(_spec())
    assert not state.ready
    assert state.status == "failed"
    # The operator gets git's message, not "still provisioning".
    assert "could not read Username" in state.error


def test_the_opencode_config_carries_the_gateway_and_the_fleet_mcp_server(no_real_cluster):  # noqa: ARG001
    document = opencode_config(_spec(), get_settings())
    # The agent holds a project-scoped gateway token, never a vendor key — and
    # it is a reference to the run's environment, not a value on disk: every
    # project in a shared environment runs as the same user.
    assert document["provider"]["fleet"]["options"]["apiKey"] == "{env:FLEET_LLM_TOKEN}"
    assert "project-token-value" not in json.dumps(document)
    assert document["model"] == "fleet/local-coder"
    server = document["mcp"]["fleet"]
    # Streamable HTTP at /mcp: what OpenCode 1.18.31's MCP client actually speaks.
    assert server["type"] == "remote"
    assert server["url"].endswith("/mcp")
    assert server["enabled"] is True


def test_create_writes_the_config_where_t3_sessions_will_find_it(no_real_cluster):  # noqa: ARG001
    """A session opened on the checkout must see the fleet tools unconfigured."""
    environment = FakeT3Environment()
    provider = _provider(environment)
    provider.create(_spec())
    writes = "\n".join(script for script, _ in environment.writes)
    commands = "\n".join(environment.commands)
    assert "/projects/demo/.fleet/opencode.json" in writes
    # The project-root copy is what opencode reads for a session in that dir.
    assert "cp /projects/demo/.fleet/opencode.json /projects/demo/opencode.json" in writes
    # Fleet's own files are excluded from git locally, so the checkout stays
    # clean for T3 and for an agent looking at `git status`.
    assert ".git/info/exclude" in commands
    for entry in ("opencode.json", ".fleet/"):
        assert entry in commands


def test_a_non_empty_checkout_directory_does_not_break_the_clone(no_real_cluster):  # noqa: ARG001
    """`.fleet/` exists before the clone, and `git clone` refuses a non-empty path.

    That is not hypothetical: it is how the first live provisioning failed —
    `fatal: destination path '/projects/checkout-alpha' already exists and is not
    an empty directory`. The clone goes to a scratch directory and is copied in.
    """
    environment = FakeT3Environment()
    provider = _provider(environment)
    provider.create(_spec())
    clone = next(command for command in environment.commands if "git clone" in command)
    assert "scratch" in clone
    assert 'cp -a "$scratch/repo/." /projects/demo/' in clone


def test_credentials_come_from_the_control_plane_namespace(no_real_cluster):  # noqa: ARG001
    """A checkout has no namespace, so the Secret is read where it is stored.

    The DevPod path copies a project's Secret into the workspace's namespace and
    lets the pod resolve it. This provider must not try that — the reference is a
    directory name, and there is no such namespace — so it reads the Secret from
    the control plane's own namespace and writes it into the checkout.
    """

    from fleet_core.workspaces.base import SecretRef

    settings = get_settings()
    assert _provider().credentials_in_namespace is False
    no_real_cluster.core.secrets[(settings.namespace, "demo-credentials")] = _secret(
        "demo-credentials", settings.namespace, {"GITHUB_TOKEN": "not-a-real-token"}
    )
    try:
        environment = FakeT3Environment()
        provider = _provider(environment)
        provider.create(
            _spec(
                secrets=[
                    SecretRef(
                        name="github",
                        secret_name="demo-credentials",
                        key="GITHUB_TOKEN",
                        env_var="GITHUB_TOKEN",
                    )
                ]
            )
        )
    finally:
        no_real_cluster.core.secrets.pop((settings.namespace, "demo-credentials"), None)

    written = [
        payload for script, payload in environment.writes if "credentials.env" in script
    ]
    assert written, "no credentials file was written"
    assert "GITHUB_TOKEN=not-a-real-token" in written[0]
    # Written over stdin, never in a command line: argv is in the API server's log.
    assert not any("not-a-real-token" in command for command in environment.commands)


def _secret(name: str, namespace: str, values: dict[str, str]):
    import base64

    from kubernetes import client

    return client.V1Secret(
        metadata=client.V1ObjectMeta(name=name, namespace=namespace),
        data={key: base64.b64encode(value.encode()).decode() for key, value in values.items()},
    )


def test_stop_says_there_is_nothing_to_stop(no_real_cluster):  # noqa: ARG001
    provider = _provider()
    with pytest.raises(ProviderError) as caught:
        provider.stop("demo")
    assert "shared T3 environment" in str(caught.value)


def test_destroy_removes_the_registration_and_the_directory(no_real_cluster):  # noqa: ARG001
    environment = FakeT3Environment()
    provider = _provider(environment)
    provider.create(_spec())
    environment.commands.clear()

    provider.destroy("demo")
    joined = "\n".join(environment.commands)
    assert "t3 project remove demo --force" in joined
    assert "rm -rf -- /projects/demo /projects/.fleet/demo" in joined


def test_capabilities_do_not_claim_isolation_this_provider_does_not_have(no_real_cluster):  # noqa: ARG001
    capabilities = _provider().capabilities()
    assert capabilities["isolation"].startswith("none of its own")
    assert capabilities["network_policy"] is False
    assert capabilities["start_stop"] is False
    assert capabilities["t3"] is True
    assert capabilities["projects_dir"] == "/projects"


def test_status_is_stopped_when_the_environment_is_gone(no_real_cluster):  # noqa: ARG001
    no_real_cluster.pods["fleet-test"] = ""
    try:
        state = _provider().status("demo")
        assert state.status == "stopped"
        assert state.ready is False
    finally:
        no_real_cluster.pods.pop("fleet-test", None)


def test_the_agent_layer_follows_the_provider_layout(no_real_cluster):  # noqa: ARG001
    """A task in a checkout workspace works in the checkout, not in /workspaces."""
    provider = _provider()
    agent = OpenCodeProvider(provider)
    spec = AgentSpec(
        agent_id="agt_demo",
        name="backend",
        project_id="prj_demo",
        project_name="demo",
        workspace_reference="demo",
        model="local-coder",
    )
    assert agent._fleet_home(spec) == "/projects/demo/.fleet"
    assert agent._worktree_path(spec, "tsk_1") == "/projects/.fleet/demo/worktrees/backend-tsk_1"
    provider.create(_spec())
    assert agent.start(spec) == "idle"
    # The start probe looks for the tools where this provider says they are,
    # not in a per-workspace toolchain directory that a checkout does not have.
    commands = provider._runner.commands  # noqa: SLF001 - the fake is the assertion
    assert any("test -x /usr/local/bin/opencode" in command for command in commands)
    assert not any("/tools/bin/opencode" in command for command in commands)


def test_re_registering_an_existing_checkout_is_not_a_failure(no_real_cluster):  # noqa: ARG001
    """Measured against v0.0.40: `t3 project add` exits **non-zero** when the root
    is already a project (`ProjectAlreadyExistsError`).

    The desired-state re-provision has to converge on that, not call it failed —
    which is what happened the first time a checkout was re-applied.
    """
    script = _provider()._register_script("demo")

    assert "ProjectAlreadyExistsError" in script
    # and the already-exists branch is the success branch
    branch = script.split("elif echo", 1)[1].split("else echo", 1)[0]
    assert "T3_REGISTERED" in branch
    assert "T3_REGISTER_FAILED" not in branch


def test_a_register_failure_keeps_the_commands_own_message(no_real_cluster):  # noqa: ARG001
    """`t3 project add did not register the checkout` sent a reader to the wrong
    place; the command's own last line says what happened."""
    environment = FakeT3Environment()
    environment.register_errors = True
    state = _provider(environment).create(_spec())

    assert not state.ready
    assert state.status == "failed"
    assert "something went wrong" in state.error
