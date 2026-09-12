"""The agent permission policy."""

from __future__ import annotations

import pytest


def test_defaults_are_restrictive():
    from agentforge_shared.permissions import AgentPermissions

    policy = AgentPermissions()
    assert policy.filesystem.workspace is True
    assert policy.filesystem.host is False
    assert policy.filesystem.other_projects is False
    assert policy.terminal.enabled is True
    assert policy.network.mode == "restricted"
    assert policy.kubernetes.enabled is False
    assert policy.git.enabled is True
    assert policy.git.push is False
    assert policy.secrets.enabled is False


def test_network_mode_is_validated():
    from agentforge_shared.permissions import AgentPermissions
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AgentPermissions(network={"mode": "wide-open"})


def test_from_dict_tolerates_a_partial_policy():
    """A stored policy predating a new field must still load with defaults."""
    from agentforge_shared.permissions import AgentPermissions

    policy = AgentPermissions.from_dict({"network": {"mode": "open"}})
    assert policy.network.mode == "open"
    assert policy.filesystem.host is False
    assert policy.kubernetes.enabled is False


def test_from_dict_handles_none():
    from agentforge_shared.permissions import AgentPermissions

    assert AgentPermissions.from_dict(None).network.mode == "restricted"


def test_egress_helper():
    from agentforge_shared.permissions import AgentPermissions

    assert AgentPermissions().allows_network_egress() is True
    assert AgentPermissions(network={"mode": "none"}).allows_network_egress() is False
