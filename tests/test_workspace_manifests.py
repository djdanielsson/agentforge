"""Workspace manifests carry our isolation guarantees."""

from __future__ import annotations

import pytest


@pytest.fixture()
def manifests():
    pytest.importorskip("kubernetes")
    from agentforge_workspaces import manifests as m

    return m


def test_pod_never_mounts_a_host_path_and_has_no_api_token(manifests):
    pod = manifests.build_pod(
        "af-demo-ws",
        "af-demo",
        image="codercom/code-server:latest",
        pvc_name="af-demo-workspace",
        git_repository="https://github.com/example/demo",
        git_revision="main",
    )

    assert pod.spec.automount_service_account_token is False
    for volume in pod.spec.volumes:
        assert volume.host_path is None, "workspaces must never mount the host"

    main = next(c for c in pod.spec.containers if c.name == "code-server")
    mounts = {m.mount_path for m in main.volume_mounts}
    assert mounts == {"/workspace"}
    assert main.security_context.allow_privilege_escalation is False
    assert main.security_context.capabilities.drop == ["ALL"]


def test_git_bootstrap_runs_as_the_workspace_uid(manifests):
    pod = manifests.build_pod(
        "af-demo-ws",
        "af-demo",
        image="codercom/code-server:latest",
        pvc_name="af-demo-workspace",
        git_repository="https://github.com/example/demo",
    )
    clone = next(c for c in pod.spec.init_containers if c.name == "git-clone")
    assert clone.security_context.run_as_user == 1000
    assert "git clone" in " ".join(clone.args)


def test_no_init_container_without_a_repository(manifests):
    pod = manifests.build_pod("af-x-ws", "af-x", image="img", pvc_name="af-x-workspace")
    assert not pod.spec.init_containers


def test_pvc_requests_the_configured_storage(manifests):
    pvc = manifests.build_pvc("af-x-workspace", "af-x", "10Gi", "local-path")
    assert pvc.spec.resources.requests == {"storage": "10Gi"}
    assert pvc.spec.storage_class_name == "local-path"
    assert pvc.spec.access_modes == ["ReadWriteOnce"]
