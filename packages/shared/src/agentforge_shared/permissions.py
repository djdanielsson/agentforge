"""The agent permission policy.

Defence in depth: this policy is declared per agent, and the workspace provider
turns it into concrete enforcement — pod security context, NetworkPolicy, which
volumes exist, and which credentials get injected. A policy that is only
documented is not a policy, so every field here must map to something the
provider actually does.

Defaults are deliberately restrictive: a new agent gets its workspace, git and a
terminal, and nothing else.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _PolicyModel(BaseModel):
    """Permission objects are mutated in place by callers, so every assignment
    must be validated. Without this, a typo like mode="widopen" would sail
    through and later be treated as an unknown mode."""

    model_config = ConfigDict(validate_assignment=True)


class FilesystemPermission(_PolicyModel):
    """Where the agent's process can see."""

    workspace: bool = True
    """Mount /workspace. This is the agent's entire universe."""
    host: bool = False
    """Mount any host path. Must stay false; there is no safe version of this."""
    other_projects: bool = False
    """Read another project's volume. False keeps projects isolated from each other."""


class TerminalPermission(_PolicyModel):
    enabled: bool = True
    """Run commands in the workspace. Also gates `exec` on the provider API."""


class NetworkPermission(_PolicyModel):
    mode: str = Field(default="restricted", pattern="^(none|restricted|open)$")
    """none: no egress. restricted: DNS plus the git remote and the LLM gateway
    only. open: unrestricted egress."""


class KubernetesPermission(_PolicyModel):
    enabled: bool = False
    """Access the cluster API from inside the workspace. Off, and the service
    account token is not mounted, so there is nothing to steal."""


class GitPermission(_PolicyModel):
    enabled: bool = True
    push: bool = False
    """Write access to the remote. Agents commit locally; pushing is opt-in per
    project, because a push is the point where agent output leaves the sandbox."""


class SecretsPermission(_PolicyModel):
    enabled: bool = False
    """Inject project secrets. Off means the workspace starts with no credentials
    at all, which is the right default for code the agent may have written."""


class AgentPermissions(_PolicyModel):
    """The full policy. Serialised to JSON on the agent row."""

    filesystem: FilesystemPermission = Field(default_factory=FilesystemPermission)
    terminal: TerminalPermission = Field(default_factory=TerminalPermission)
    network: NetworkPermission = Field(default_factory=NetworkPermission)
    kubernetes: KubernetesPermission = Field(default_factory=KubernetesPermission)
    git: GitPermission = Field(default_factory=GitPermission)
    secrets: SecretsPermission = Field(default_factory=SecretsPermission)

    @classmethod
    def from_dict(cls, value: dict | None) -> AgentPermissions:
        """Tolerant loader: an agent row predating a new field still loads."""
        return cls.model_validate(value or {})

    def allows_network_egress(self) -> bool:
        return self.network.mode != "none"


#: What a brand new agent gets when the caller does not specify a policy.
DEFAULT_PERMISSIONS = AgentPermissions()
