"""The provider contract.

Everything above this line is provider-agnostic. If a feature cannot be
expressed here, it does not belong in the workspace layer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from agentforge_shared.permissions import AgentPermissions
from agentforge_shared.schemas import ResolvedSecret


class ProviderError(RuntimeError):
    """A provider could not satisfy a request. Carries context for the UI."""

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


@dataclass(slots=True)
class WorkspaceSpec:
    """Everything a provider needs to build one project's world."""

    project_id: str
    slug: str
    name: str
    reference: str
    """The provider-native identifier: a namespace, a container name, a path.
    Computed by the caller so it is stable across retries."""

    repository_url: str | None = None
    revision: str = "main"

    image: str = "ghcr.io/coder/code-server:latest"
    #: How the agent server runs its agent inside the pod. `local` keeps it in the
    #: pod, which is already the sandbox; `docker` needs a socket the pod has not
    #: got, and leaves every conversation stuck in STARTING.
    agent_runtime: str = "local"
    agent_image: str = "ghcr.io/all-hands-ai/openhands:latest"

    cpu_request: str = "500m"
    memory_request: str = "1Gi"
    cpu_limit: str = "2"
    memory_limit: str = "4Gi"
    storage: str = "10Gi"
    storage_class: str | None = None

    code_server_port: int = 8080
    agent_server_port: int = 3000
    #: uid/gid the agent runtime runs as. 0 is root, which the OpenHands image
    #: requires; a rootless image would use its own user.
    agent_uid: int = 0
    agent_gid: int = 0

    permissions: AgentPermissions = field(default_factory=AgentPermissions)
    #: References only. The provider resolves them from its own secret store.
    secrets: list[ResolvedSecret] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)

    labels: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class WorkspaceState:
    """What a provider reports back. Never contains a secret value."""

    reference: str
    provider: str
    status: str
    ready: bool = False
    code_server_url: str | None = None
    agent_server_url: str | None = None
    #: Provider-native object names, so the caller can address the workspace
    #: later without knowing how the provider names things.
    pvc_name: str | None = None
    pod_name: str | None = None
    service_name: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class WorkspaceProvider(ABC):
    """Create, run and tear down isolated workspaces.

    Implementations must be idempotent: `create` on an existing workspace
    converges rather than failing, because the orchestrator retries.
    """

    name: ClassVar[str] = "abstract"

    @abstractmethod
    def create(self, spec: WorkspaceSpec) -> WorkspaceState:
        """Create the workspace if absent, then return its state."""

    @abstractmethod
    def start(self, reference: str) -> WorkspaceState:
        """Bring an existing stopped workspace back up."""

    @abstractmethod
    def stop(self, reference: str) -> None:
        """Stop without destroying. Data survives."""

    @abstractmethod
    def destroy(self, reference: str) -> None:
        """Delete the workspace and everything in it. Must not raise if absent."""

    @abstractmethod
    def exec(self, reference: str, command: list[str], *, container: str | None = None) -> str:
        """Run a command inside the workspace and return its combined output."""

    @abstractmethod
    def get_status(self, reference: str) -> WorkspaceState:
        """Observe reality. Never raises for a missing workspace."""

    @abstractmethod
    def inject_secrets(self, reference: str, secrets: list[ResolvedSecret]) -> None:
        """Make secret references resolvable inside the workspace.

        Implementations project refs into their own secret mechanism. They must
        never receive, store or log a secret value.
        """

    def capabilities(self) -> dict[str, Any]:
        """Advertised honestly so the UI can disable what a backend cannot do."""
        return {
            "provider": self.name,
            "isolation": "unknown",
            "secrets": False,
            "network_policy": False,
            "exec": True,
            "persistent_volumes": False,
        }
