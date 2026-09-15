"""The provider boundaries (SPEC §6, §9, §46).

These tests are about the *interfaces*: that a provider can be swapped, that a
provider which cannot do something says so, and that the opencode provider's
commands are well formed.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fakes import FakeWorkspaceProvider, agent_spec, task_request
from fleet_core.agents import AGENT_PROVIDERS, get_agent_provider
from fleet_core.agents.base import AgentError, AgentProvider
from fleet_core.agents.opencode import OpenCodeProvider, _last_assistant_text
from fleet_core.agents.t3code import T3CodeProvider
from fleet_core.workspaces import WorkspaceProvider, WorkspaceSpec, describe_providers
from fleet_core.workspaces.base import ProviderError


def test_fake_provider_satisfies_the_interface():
    provider = FakeWorkspaceProvider()
    assert isinstance(provider, WorkspaceProvider)
    # Every abstract method is implemented, not inherited as a stub.
    for name in ("create", "status", "destroy", "execute", "get_logs"):
        assert getattr(type(provider), name) is not getattr(WorkspaceProvider, name)


def test_every_registered_agent_provider_implements_the_interface():
    workspaces = FakeWorkspaceProvider()
    for name in AGENT_PROVIDERS:
        provider = get_agent_provider(name, workspaces)
        assert isinstance(provider, AgentProvider)
        # The declared capability must match the behaviour, not just the docstring.
        if not provider.executes_tasks:
            with pytest.raises(AgentError):
                provider.execute_task(agent_spec(), task_request())


def test_unknown_provider_names_raise_rather_than_fall_back_silently():
    with pytest.raises(AgentError):
        get_agent_provider("nope", FakeWorkspaceProvider())
    with pytest.raises(ProviderError):
        from fleet_core.workspaces import get_workspace_provider

        get_workspace_provider("nope")


def test_t3_code_declares_itself_a_control_surface():
    capabilities = T3CodeProvider(FakeWorkspaceProvider()).capabilities()
    assert capabilities["executes_tasks"] is False
    assert capabilities["provides_ui"] is True


def test_workspace_provider_defaults_are_honest():
    """A provider that cannot stop a workspace must say so, not pretend."""

    class Minimal(WorkspaceProvider):
        name = "minimal"

        def create(self, spec: WorkspaceSpec):
            raise NotImplementedError

        def status(self, reference: str):
            raise NotImplementedError

        def destroy(self, reference: str) -> None:
            raise NotImplementedError

        def execute(self, reference, command, *, container=None):
            raise NotImplementedError

        def get_logs(self, reference, *, tail: int = 200) -> str:
            raise NotImplementedError

    minimal = Minimal()
    assert minimal.capabilities()["start_stop"] is False
    with pytest.raises(ProviderError):
        minimal.start("x")
    with pytest.raises(ProviderError):
        minimal.stop("x")
    assert minimal.connect("x") == ""


def test_opencode_provider_prepares_a_worktree_and_invokes_the_cli():
    workspaces = FakeWorkspaceProvider()
    provider = OpenCodeProvider(workspaces)
    workspaces.respond(
        "git worktree add", "WORKTREE_READY /workspaces/fleet-demo/.fleet/worktrees/backend\n"
    )
    workspaces.respond(
        "opencode run",
        '{"type":"text","text":"created hello.txt"}\n',
    )

    outcome = provider.execute_task(agent_spec(), task_request())
    assert outcome.status == "completed"

    script = " ".join(" ".join(command) for _, command in workspaces.commands)
    assert "git worktree add" in script
    assert "opencode run" in script
    # The agent is pointed at the fleet gateway's logical model, not a provider.
    assert "--model fleet/local-coder" in script
    assert "FLEET_LLM_TOKEN=" in script
    # And it picks up the project's credentials from its own workspace volume.
    assert "credentials.env" in script


def test_opencode_provider_fails_loudly_without_a_repository():
    workspaces = FakeWorkspaceProvider()
    provider = OpenCodeProvider(workspaces)
    workspaces.respond("git worktree add", "NO_REPO\n", exit_code=3)
    outcome = provider.execute_task(agent_spec(), task_request())
    assert outcome.status == "failed"
    assert outcome.detail["stage"] == "worktree"


def test_opencode_provider_cannot_accept_interactive_input():
    """`opencode run` is headless; claiming otherwise would be a lie."""
    provider = OpenCodeProvider(FakeWorkspaceProvider())
    assert provider.send_input(agent_spec(), "hello") is False
    assert provider.capabilities()["send_input"] is False


def test_json_event_stream_is_parsed():
    raw = "\n".join(
        [
            '{"type":"step_start"}',
            '{"type":"text","text":"first"}',
            '{"type":"text","text":"second and final"}',
        ]
    )
    assert _last_assistant_text(raw) == "second and final"
    # Non-JSON output is passed through rather than dropped.
    assert _last_assistant_text("plain text") == "plain text"


def test_devpod_injects_credentials_by_file_not_by_argv(tmp_path, monkeypatch):
    """The credential reaches the pod, and the value never appears in `argv`.

    `--workspace-env` would put the secret in the process's command line, which
    any process on the machine can read. The env-file form keeps it out.
    """
    import fleet_core.secrets as fleet_secrets
    from fleet_core.config import get_settings
    from fleet_core.workspaces.base import SecretRef
    from fleet_core.workspaces.devpod import DevPodKubernetesProvider

    settings = get_settings(refresh=True)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(
        fleet_secrets, "secret_values", lambda namespace, name: {"github": "a-secret-value"}
    )

    provider = DevPodKubernetesProvider(settings)
    spec = WorkspaceSpec(
        project_id="prj_1",
        project_name="demo",
        reference="fleet-demo",
        secrets=[
            SecretRef(
                name="github",
                secret_name="demo-credentials",
                key="github",
                env_var="GITHUB_TOKEN",
            )
        ],
    )
    args = provider._up_args(spec)

    assert "--workspace-env-file" in args
    assert "--workspace-env" not in args
    assert "a-secret-value" not in " ".join(args)

    written = args[args.index("--workspace-env-file") + 1]
    assert Path(written).read_text().strip() == "GITHUB_TOKEN=a-secret-value"
    # Only the owning process should be able to read it.
    assert Path(written).stat().st_mode & 0o077 == 0
    # And the namespace it is provisioned into is the project's own boundary.
    assert "--provider-option" in args
    assert "KUBERNETES_NAMESPACE=fleet-demo" in args


def test_a_workspace_with_no_credentials_gets_no_env_file(tmp_path, monkeypatch):
    from fleet_core.config import get_settings
    from fleet_core.workspaces.devpod import DevPodKubernetesProvider

    settings = get_settings(refresh=True)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    provider = DevPodKubernetesProvider(settings)
    spec = WorkspaceSpec(project_id="prj_1", project_name="demo", reference="fleet-demo")
    assert "--workspace-env-file" not in provider._up_args(spec)


def test_credentials_are_written_into_the_workspace_over_stdin(tmp_path, monkeypatch):
    """The only delivery path that works, and it keeps the value out of argv.

    DevPod's `--workspace-env-file` is accepted but the variables did not appear
    in a live container's environment, and a Kubernetes exec cannot set an
    environment for the process it starts. So the credential is written into the
    workspace's own volume over stdin, where the API server never records it.
    """
    import fleet_core.secrets as fleet_secrets
    from fleet_core.config import get_settings
    from fleet_core.workspaces.base import ExecResult, SecretRef
    from fleet_core.workspaces.devpod import DevPodKubernetesProvider

    settings = get_settings(refresh=True)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(
        fleet_secrets, "secret_values", lambda namespace, name: {"github": "a-secret-value"}
    )

    provider = DevPodKubernetesProvider(settings)
    captured: dict = {}

    def fake_execute(reference, command, *, container=None, stdin_data=None):
        captured["command"] = command
        captured["stdin"] = stdin_data
        return ExecResult(" ".join(command), 0, stdout="")

    monkeypatch.setattr(provider, "execute", fake_execute)
    spec = WorkspaceSpec(
        project_id="prj_1",
        project_name="demo",
        reference="fleet-demo",
        secrets=[
            SecretRef(
                name="github", secret_name="demo-credentials", key="github", env_var="GITHUB_TOKEN"
            )
        ],
    )

    assert provider._write_credential_env(spec) is True
    script = " ".join(captured["command"])
    assert "credentials.env" in script
    assert "umask 077" in script
    # The value travels on stdin, not in the command the API server records.
    assert "a-secret-value" not in script
    assert captured["stdin"] == "GITHUB_TOKEN=a-secret-value\n"


def test_a_project_without_credentials_writes_nothing(tmp_path, monkeypatch):
    from fleet_core.config import get_settings
    from fleet_core.workspaces.devpod import DevPodKubernetesProvider

    settings = get_settings(refresh=True)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    provider = DevPodKubernetesProvider(settings)
    spec = WorkspaceSpec(project_id="prj_1", project_name="demo", reference="fleet-demo")
    assert provider._write_credential_env(spec) is False
    assert provider._credential_values(spec) == {}


def test_providers_report_class_capabilities_without_raising():
    """An unavailable provider is reported, not hidden behind an exception."""
    report = describe_providers()
    assert report
    for entry in report:
        assert "name" in entry
        assert "capabilities" in entry or "error" in entry


def test_provider_methods_are_declared_on_the_interface():
    """The interface is the contract: these names may not drift per provider."""
    expected = {
        "create",
        "start",
        "stop",
        "restart",
        "destroy",
        "status",
        "connect",
        "execute",
        "get_logs",
    }
    declared = set(dir(WorkspaceProvider))
    assert expected <= declared
    for name in inspect.signature(WorkspaceProvider).parameters:
        assert name == "self"
