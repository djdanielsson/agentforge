"""The manifests are where the permission policy becomes real.

If a policy field does not change a manifest, it is documentation rather than
enforcement, and these tests exist to catch that.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def manifests():
    pytest.importorskip("kubernetes")
    from agentforge_workspaces import manifests as m

    return m


@pytest.fixture()
def permissions():
    from agentforge_shared.permissions import AgentPermissions

    return AgentPermissions()


def _pod(manifests, permissions, **overrides):
    kwargs = dict(
        name="af-demo-ws",
        namespace="af-demo",
        image="codercom/code-server:latest",
        agent_image="ghcr.io/all-hands-ai/openhands:latest",
        pvc_name="af-demo-workspace",
        permissions=permissions,
    )  # agent_uid/agent_gid default to the OpenHands image's user
    kwargs.update(overrides)
    return manifests.build_pod(**kwargs)


# --- isolation invariants ---------------------------------------------------


def test_pod_never_mounts_a_host_path(manifests, permissions):
    pod = _pod(manifests, permissions, git_repository="https://github.com/example/demo")
    for volume in pod.spec.volumes:
        assert volume.host_path is None, "workspaces must never mount the host"


def test_no_api_token_is_mounted_by_default(manifests, permissions):
    pod = _pod(manifests, permissions)
    assert pod.spec.automount_service_account_token is False


def test_requesting_host_filesystem_is_refused_not_downgraded(manifests, permissions):
    """Silently ignoring the flag would leave a user believing isolation held."""
    permissions.filesystem.host = True
    with pytest.raises(ValueError, match="never mount the host"):
        _pod(manifests, permissions)


def test_requesting_other_projects_is_refused(manifests, permissions):
    permissions.filesystem.other_projects = True
    with pytest.raises(ValueError, match="project isolation"):
        _pod(manifests, permissions)


def test_workspace_mount_is_required(manifests, permissions):
    permissions.filesystem.workspace = False
    with pytest.raises(ValueError, match="needs somewhere to work"):
        _pod(manifests, permissions)


# --- policy mapping ---------------------------------------------------------


def test_kubernetes_access_grants_the_service_account_token(manifests, permissions):
    permissions.kubernetes.enabled = True
    pod = _pod(manifests, permissions)
    assert pod.spec.automount_service_account_token is True


def test_all_capabilities_are_dropped_and_escalation_is_off(manifests, permissions):
    """These, not the uid, are what make a workspace safe."""
    pod = _pod(manifests, permissions)
    for container in pod.spec.containers:
        assert container.security_context.allow_privilege_escalation is False
        assert container.security_context.capabilities.drop == ["ALL"]
    # fsGroup keeps the shared volume usable by both containers.
    assert pod.spec.security_context.fs_group == 1000


def test_code_server_runs_as_an_explicit_non_root_uid(manifests, permissions):
    pod = _pod(manifests, permissions)
    code_server = next(c for c in pod.spec.containers if c.name == "code-server")
    assert code_server.security_context.run_as_user == 1000
    assert code_server.security_context.run_as_non_root is True


def test_the_agent_runtime_runs_as_root_because_its_image_requires_it(manifests, permissions):
    """The one place non-root is not achievable.

    The OpenHands image declares User: root and its entrypoint exits with
    "The OpenHands entrypoint.sh must run as root" for any other uid. Found by
    deploying: uid 1000 failed with EACCES on the entrypoint, uid 42420 (which
    owns the file) started the entrypoint only to be told it must be root.
    """
    pod = _pod(manifests, permissions)
    agent = next(c for c in pod.spec.containers if c.name == "openhands")
    assert agent.security_context.run_as_user == 0
    assert agent.security_context.run_as_non_root is None


def test_root_gets_only_the_capability_it_needs(manifests, permissions):
    """CAP_DAC_OVERRIDE is not decorative.

    The entrypoint is mode 770 owned by uid 42420. Dropping ALL capabilities
    removes DAC_OVERRIDE, and without it root cannot execute a file it does not
    own -- which is why `drop: [ALL]` on its own produced

      exec: "/app/entrypoint.sh": permission denied

    Nothing else is added back.
    """
    pod = _pod(manifests, permissions)
    agent = next(c for c in pod.spec.containers if c.name == "openhands")
    caps = agent.security_context.capabilities
    assert caps.drop == ["ALL"]
    assert caps.add == ["DAC_OVERRIDE"]


def test_a_rootless_agent_image_can_be_selected_instead(manifests, permissions):
    """A non-zero uid switches to the non-root posture with no extra caps."""
    pod = _pod(manifests, permissions, agent_uid=1001, agent_gid=1001)
    agent = next(c for c in pod.spec.containers if c.name == "openhands")
    assert agent.security_context.run_as_user == 1001
    assert agent.security_context.run_as_non_root is True
    assert agent.security_context.capabilities.drop == ["ALL"]
    assert not agent.security_context.capabilities.add


def test_the_agent_container_is_not_granted_extra_privileges(manifests, permissions):
    """Running as the image's user must not become a way to gain capabilities."""
    pod = _pod(manifests, permissions)
    agent = next(c for c in pod.spec.containers if c.name == "openhands")
    assert agent.security_context.privileged is None
    # Root in the container must not become root on the node.
    for volume in pod.spec.volumes:
        assert volume.host_path is None
    assert pod.spec.host_pid is None
    assert pod.spec.host_network is None


def test_both_containers_mount_only_the_workspace(manifests, permissions):
    pod = _pod(manifests, permissions)
    names = {c.name for c in pod.spec.containers}
    assert names == {"code-server", "openhands"}
    for container in pod.spec.containers:
        assert {m.mount_path for m in container.volume_mounts} == {"/workspace"}


def test_git_push_flag_is_communicated_to_the_workspace(manifests, permissions):
    permissions.git.push = True
    pod = _pod(manifests, permissions)
    env = {e.name: e.value for e in pod.spec.containers[0].env if e.value is not None}
    assert env["GIT_PUSH_ENABLED"] == "true"


# --- secrets ----------------------------------------------------------------


def test_secrets_are_injected_as_references_only(manifests, permissions):
    from agentforge_shared.schemas import ResolvedSecret

    permissions.secrets.enabled = True
    pod = _pod(
        manifests,
        permissions,
        secrets=[ResolvedSecret(env_var="GITHUB_TOKEN", secret_name="gh", key="token")],
    )
    env = {e.name: e for e in pod.spec.containers[0].env}
    assert "GITHUB_TOKEN" in env
    source = env["GITHUB_TOKEN"].value_from.secret_key_ref
    assert source.name == "gh"
    assert source.key == "token"
    # The value is resolved by the kubelet; AgentForge never sees it.
    assert env["GITHUB_TOKEN"].value is None


def test_secrets_are_withheld_when_the_policy_says_so(manifests, permissions):
    from agentforge_shared.schemas import ResolvedSecret

    assert permissions.secrets.enabled is False
    pod = _pod(
        manifests,
        permissions,
        secrets=[ResolvedSecret(env_var="GITHUB_TOKEN", secret_name="gh", key="token")],
    )
    assert "GITHUB_TOKEN" not in {e.name for e in pod.spec.containers[0].env}
    assert pod._agentforge_warnings, "withholding a secret must not be silent"


def test_required_secret_is_not_optional_in_the_reference(manifests, permissions):
    from agentforge_shared.schemas import ResolvedSecret

    permissions.secrets.enabled = True
    pod = _pod(
        manifests,
        permissions,
        secrets=[ResolvedSecret(env_var="DB_URL", secret_name="db", key="url", required=True)],
    )
    env = {e.name: e for e in pod.spec.containers[0].env}
    assert env["DB_URL"].value_from.secret_key_ref.optional is False


# --- network policy ---------------------------------------------------------


def test_network_mode_none_denies_all_egress(manifests, permissions):
    permissions.network.mode = "none"
    policy = manifests.build_network_policy("af-demo-egress", "af-demo", permissions)
    assert policy.spec.policy_types == ["Ingress", "Egress"]
    assert policy.spec.egress == []


def test_network_mode_restricted_allows_dns_and_https(manifests, permissions):
    assert permissions.network.mode == "restricted"
    policy = manifests.build_network_policy("af-demo-egress", "af-demo", permissions)
    routes = policy.spec.egress
    assert len(routes) == 2
    ports = {p.port for p in routes[0].ports}
    assert ports == {80, 443, 22}
    assert {p.port for p in routes[1].ports} == {53}


def test_network_mode_open_creates_no_policy(manifests, permissions):
    permissions.network.mode = "open"
    assert manifests.build_network_policy("af-demo-egress", "af-demo", permissions) is None


def test_invalid_network_mode_is_rejected(manifests, permissions):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        permissions.network.mode = "whatever"


# --- git bootstrap ----------------------------------------------------------


def test_git_bootstrap_runs_as_the_workspace_uid(manifests, permissions):
    pod = _pod(manifests, permissions, git_repository="https://github.com/example/demo")
    clone = next(c for c in pod.spec.init_containers if c.name == "git-clone")
    assert clone.security_context.run_as_user == 1000
    assert clone.security_context.allow_privilege_escalation is False
    assert "git clone" in " ".join(clone.args)


def test_no_init_container_without_a_repository(manifests, permissions):
    assert not _pod(manifests, permissions).spec.init_containers


def test_pvc_requests_the_configured_storage(manifests):
    pvc = manifests.build_pvc("af-x-workspace", "af-x", "10Gi", "local-path")
    assert pvc.spec.resources.requests == {"storage": "10Gi"}
    assert pvc.spec.storage_class_name == "local-path"
    assert pvc.spec.access_modes == ["ReadWriteOnce"]


def test_service_exposes_both_containers(manifests):
    service = manifests.build_service("af-x-ws", "af-x", {"http": 8080, "agent": 3000})
    assert {p.name for p in service.spec.ports} == {"http", "agent"}
