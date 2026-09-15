"""The workspace-provider interface (SPEC §6).

The rest of the control plane only ever sees this. Nothing above it knows that
DevPod exists, or that DevPod happens to produce a Kubernetes pod.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ProviderError(RuntimeError):
    """A provider failed for a reason the caller must surface, not swallow."""

    def __init__(self, message: str, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


@dataclass
class SecretRef:
    """A reference to a secret, never a value (SPEC §16)."""

    name: str
    secret_name: str
    key: str = "token"
    env_var: str = ""
    required: bool = False


@dataclass
class WorkspaceSpec:
    project_id: str
    project_name: str
    reference: str
    image: str = ""
    cpu: str = "500m"
    memory: str = "1Gi"
    storage: str = "5Gi"
    storage_class: str = "local-path"
    repository_url: str = ""
    repository_branch: str = "main"
    environment: dict[str, str] = field(default_factory=dict)
    secrets: list[SecretRef] = field(default_factory=list)
    t3_enabled: bool = False
    definition_dir: str = ""


@dataclass
class WorkspaceState:
    reference: str
    provider: str
    status: str = "pending"
    ready: bool = False
    pod_name: str = ""
    pvc_name: str = ""
    service_name: str = ""
    url: str = ""
    t3_url: str = ""
    error: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecResult:
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


class WorkspaceProvider(ABC):
    """Capability-based and deliberately small (SPEC §46).

    `status`, `destroy`, `execute` and `get_logs` are required: the control plane
    cannot do anything useful without them. The rest have working defaults so a
    thin provider does not have to lie about capabilities it lacks.
    """

    name: str = "unknown"

    @abstractmethod
    def create(self, spec: WorkspaceSpec) -> WorkspaceState: ...

    @abstractmethod
    def status(self, reference: str) -> WorkspaceState: ...

    @abstractmethod
    def destroy(self, reference: str) -> None: ...

    @abstractmethod
    def execute(
        self,
        reference: str,
        command: list[str],
        *,
        container: str | None = None,
        stdin_data: str | None = None,
    ) -> ExecResult:
        """Run a command inside the workspace.

        `stdin_data` exists so a secret can be written to a file in the
        workspace without passing through `argv`.
        """

    @abstractmethod
    def get_logs(self, reference: str, *, tail: int = 200) -> str: ...

    def prepare(self, spec: WorkspaceSpec) -> None:
        """Create the workspace's isolation boundary before filling it.

        Called before `create`. Provisioning needs somewhere to put a project's
        credentials *before* the pod exists — a container resolves
        `secretKeyRef` at start, so a secret copied afterwards is a race, and a
        secret copied before the namespace exists is a 404.

        Providers that create their boundary inside `create` may leave this as a
        no-op; it must be idempotent where it is implemented.
        """
        return None

    def start(self, reference: str) -> WorkspaceState:
        """Bring a stopped workspace back. Providers that cannot pause can raise."""
        raise ProviderError(f"{self.name} cannot start a stopped workspace")

    def stop(self, reference: str) -> None:
        raise ProviderError(f"{self.name} cannot stop a workspace")

    def restart(self, reference: str) -> WorkspaceState:
        self.stop(reference)
        return self.start(reference)

    def connect(self, reference: str) -> str:
        """A URL a human or another program can open. Empty when unsupported."""
        return ""

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "isolation": "unknown",
            "create": True,
            "start_stop": False,
            "exec": True,
            "persistent_volumes": False,
            "network_policy": False,
            "t3": False,
        }
