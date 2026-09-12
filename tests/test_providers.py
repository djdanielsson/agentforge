"""Provider registry and the honest parts of each implementation."""

from __future__ import annotations

import pytest


def test_registry_knows_the_three_providers():
    from agentforge_workspaces.providers import available_providers

    assert set(available_providers()) == {"kubernetes", "podman", "local"}


def test_unknown_provider_is_a_clear_error():
    from agentforge_workspaces.providers import get_provider
    from agentforge_workspaces.providers.base import ProviderError

    with pytest.raises(ProviderError, match="unknown workspace provider"):
        get_provider("nomad")


def test_kubernetes_provider_advertises_its_capabilities():
    pytest.importorskip("kubernetes")
    from agentforge_workspaces.providers import get_provider

    caps = get_provider("kubernetes").capabilities()
    assert caps["isolation"] == "namespace + pod"
    assert caps["network_policy"] is True
    assert caps["secrets"] is True


def test_local_provider_reports_no_isolation():
    from agentforge_workspaces.providers import get_provider

    caps = get_provider("local").capabilities()
    assert "none" in caps["isolation"]
    assert caps["secrets"] is False


def test_local_provider_refuses_to_hold_secrets():
    from agentforge_shared.schemas import ResolvedSecret
    from agentforge_workspaces.providers import get_provider
    from agentforge_workspaces.providers.base import ProviderError

    provider = get_provider("local")
    with pytest.raises(ProviderError, match="cannot hold secrets"):
        provider.inject_secrets("af-x", [ResolvedSecret(env_var="T", secret_name="s", key="k")])


def test_local_provider_is_refused_outside_development():
    """It has no isolation, so it must not be selectable in a real environment."""
    from agentforge_shared.config import Settings
    from agentforge_workspaces.providers.base import ProviderError
    from agentforge_workspaces.providers.local import LocalProvider

    settings = Settings(environment="production")
    with pytest.raises(ProviderError, match="refused outside"):
        LocalProvider(settings)


def test_local_provider_lifecycle_uses_a_directory(tmp_path):
    from agentforge_shared.config import Settings
    from agentforge_shared.permissions import AgentPermissions
    from agentforge_workspaces.providers.base import WorkspaceSpec
    from agentforge_workspaces.providers.local import LocalProvider

    provider = LocalProvider(Settings(local_workspace_root=str(tmp_path)))
    spec = WorkspaceSpec(
        project_id="p",
        slug="demo",
        name="Demo",
        reference="af-demo",
        permissions=AgentPermissions(),
    )
    state = provider.create(spec)
    assert state.ready is True
    assert (tmp_path / "af-demo").is_dir()

    provider.destroy("af-demo")
    assert not (tmp_path / "af-demo").exists()


def test_workspace_state_carries_the_object_names_it_created():
    """The caller must be able to address a workspace later without knowing the
    provider's naming scheme. The git manager depends on pod_name: without it
    every commit is a silent no-op."""
    from agentforge_workspaces.providers.base import WorkspaceState

    state = WorkspaceState(reference="af-x", provider="kubernetes", status="ready")
    assert state.pod_name is None
    state = WorkspaceState(
        reference="af-x", provider="kubernetes", status="ready",
        pvc_name="af-x-workspace", pod_name="af-x-ws", service_name="af-x-ws",
    )
    assert state.pod_name == "af-x-ws"


def test_provider_is_required_to_be_idempotent_for_destroy():
    """Destroying a workspace that never existed is not an error."""
    from agentforge_shared.config import Settings
    from agentforge_workspaces.providers.local import LocalProvider

    LocalProvider(Settings(local_workspace_root="/tmp/agentforge-nonexistent")).destroy("nope")
