"""The Podman provider.

Exists so the same project definition can be developed and tested on a laptop
without a cluster. Isolation is a container plus a named volume, which is weaker
than a Kubernetes namespace: there is no NetworkPolicy equivalent here, so
`network.mode` is enforced with `--network` and nothing finer.

Because the podman CLI is the interface, `inject_secrets` verifies that the
referenced podman secret exists rather than copying a value — the same contract
as Kubernetes.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Any

from agentforge_shared.enums import WorkspaceStatus
from agentforge_shared.schemas import ResolvedSecret

from .base import ProviderError, WorkspaceProvider, WorkspaceSpec, WorkspaceState

log = logging.getLogger(__name__)

PODMAN = "podman"


def _run(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    log.debug("podman %s", " ".join(args))
    return subprocess.run([PODMAN, *args], capture_output=True, text=True, check=check, timeout=300)


class PodmanProvider(WorkspaceProvider):
    name = "podman"

    def __init__(self, settings: Any | None = None) -> None:
        from agentforge_shared.config import get_settings

        self.settings = settings or get_settings()
        if shutil.which(PODMAN) is None:
            raise ProviderError(
                "the podman binary is not on PATH. Install podman, or select the "
                "kubernetes provider with AGENTFORGE_WORKSPACE_PROVIDER=kubernetes."
            )

    @staticmethod
    def _container(reference: str) -> str:
        return f"{reference}-ws"

    @staticmethod
    def _volume(reference: str) -> str:
        return f"{reference}-workspace"

    def _exists(self, reference: str) -> bool:
        result = _run(["container", "exists", self._container(reference)], check=False)
        return result.returncode == 0

    def create(self, spec: WorkspaceSpec) -> WorkspaceState:
        if spec.permissions.filesystem.host:
            raise ValueError("filesystem.host is not implementable under podman either")
        if spec.permissions.filesystem.other_projects:
            raise ValueError("filesystem.other_projects is not implementable under podman")

        if not self._exists(spec.reference):
            _run(["volume", "create", self._volume(spec.reference)], check=False)

            network = {
                "none": "none",
                "restricted": "bridge",
                "open": "host",
            }.get(spec.permissions.network.mode, "bridge")

            mount = spec.environment.get("WORKSPACE_MOUNT", "/workspace")
            args = [
                "run",
                "-d",
                "--name",
                self._container(spec.reference),
                "--network",
                network,
                "-v",
                f"{self._volume(spec.reference)}:{mount}",
                "-p",
                f"{spec.code_server_port}",
                "-p",
                f"{spec.agent_server_port}",
            ]
            for key, value in spec.environment.items():
                args += ["-e", f"{key}={value}"]
            # Only references, and only when the policy allows credentials at all.
            if spec.permissions.secrets.enabled:
                for secret in self._verified_secrets(spec.reference, spec.secrets, strict=True):
                    args += ["--secret", secret.secret_name]
            args.append(spec.image)
            _run(args)

        state = self.get_status(spec.reference)
        if spec.repository_url:
            try:
                self.exec(
                    spec.reference,
                    [
                        "sh",
                        "-c",
                        f"git clone --depth 20 --branch {spec.revision} "
                        f"{spec.repository_url} /workspace || true",
                    ],
                )
            except ProviderError as exc:
                log.warning("git bootstrap failed for %s: %s", spec.reference, exc)
        return state

    def start(self, reference: str) -> WorkspaceState:
        _run(["start", self._container(reference)], check=False)
        return self.get_status(reference)

    def stop(self, reference: str) -> None:
        _run(["stop", self._container(reference)], check=False)

    def destroy(self, reference: str) -> None:
        _run(["rm", "-f", self._container(reference)], check=False)
        _run(["volume", "rm", "-f", self._volume(reference)], check=False)

    def exec(self, reference: str, command: list[str], *, container: str | None = None) -> str:
        result = _run(["exec", self._container(reference), *command], check=False)
        if result.returncode != 0:
            raise ProviderError(
                f"command failed in {reference}: {result.stderr.strip()[:500]}",
                detail={"returncode": result.returncode},
            )
        return result.stdout

    def get_status(self, reference: str) -> WorkspaceState:
        state = WorkspaceState(
            reference=reference,
            provider=self.name,
            status=str(WorkspaceStatus.PENDING),
        )
        result = _run(["inspect", self._container(reference)], check=False)
        if result.returncode != 0:
            return state
        try:
            inspected = json.loads(result.stdout)[0]
        except (json.JSONDecodeError, IndexError) as exc:
            raise ProviderError(f"could not parse podman inspect output: {exc}") from exc

        running = bool(inspected.get("State", {}).get("Running"))
        ports = (inspected.get("NetworkSettings") or {}).get("Ports") or {}
        state.status = str(WorkspaceStatus.READY if running else WorkspaceStatus.STOPPED)
        state.ready = running
        state.detail = {"ports": ports, "image": inspected.get("ImageName")}
        return state

    def inject_secrets(self, reference: str, secrets: list[ResolvedSecret]) -> None:
        self._verified_secrets(reference, secrets, strict=True)

    def _verified_secrets(
        self, reference: str, secrets: list[ResolvedSecret], *, strict: bool
    ) -> list[ResolvedSecret]:
        usable: list[ResolvedSecret] = []
        for secret in secrets:
            result = _run(["secret", "inspect", secret.secret_name], check=False)
            if result.returncode == 0:
                usable.append(secret)
            elif secret.required and strict:
                raise ProviderError(
                    f"required podman secret {secret.secret_name} does not exist",
                    detail={"env_var": secret.env_var},
                )
            else:
                log.warning(
                    "podman secret %s missing; skipping %s", secret.secret_name, secret.env_var
                )
        return usable

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "isolation": "container + volume",
            "secrets": True,
            "network_policy": False,
            "exec": True,
            "persistent_volumes": True,
        }
