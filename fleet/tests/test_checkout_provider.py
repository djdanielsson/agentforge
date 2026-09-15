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
            if slug in self.checkouts:
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
    assert layout.repo_path == "/projects/demo/.fleet/repo"
    # The tools are in the image; there is no per-workspace toolchain.
    assert layout.bin_dir == "/usr/local/bin"


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
    assert "/projects/demo/.fleet/opencode.json" in writes
    # The project-root copy is what opencode reads for a session in that dir,
    # and it is excluded from git locally so the checkout stays clean.
    assert "cp /projects/demo/.fleet/opencode.json /projects/demo/opencode.json" in writes
    assert "/projects/demo/.git/info/exclude" in writes


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
    assert "rm -rf -- /projects/demo" in joined


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
    assert agent._worktree_path(spec, "tsk_1") == "/projects/demo/.fleet/worktrees/backend-tsk_1"
    provider.create(_spec())
    assert agent.start(spec) == "idle"
    # The start probe looks for the tools where this provider says they are,
    # not in a per-workspace toolchain directory that a checkout does not have.
    commands = provider._runner.commands  # noqa: SLF001 - the fake is the assertion
    assert any("test -x /usr/local/bin/opencode" in command for command in commands)
    assert not any("/tools/bin/opencode" in command for command in commands)
