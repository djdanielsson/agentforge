"""The local provider. Development only.

Runs commands in a directory on the host with no isolation whatsoever. It exists
so the control plane can be exercised end to end without a cluster or a
container runtime, and it is deliberately honest about what it is.

It refuses to be selected when AGENTFORGE_ENVIRONMENT is anything but
development, and it refuses to handle secrets, because there is nowhere to keep
them that the agent cannot read.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agentforge_shared.enums import WorkspaceStatus
from agentforge_shared.schemas import ResolvedSecret

from .base import ProviderError, WorkspaceProvider, WorkspaceSpec, WorkspaceState

log = logging.getLogger(__name__)

#: Environments where an isolation-free provider is tolerable. `test` is here
#: because the suite exercises the lifecycle against a real directory.
NON_PRODUCTION_ENVIRONMENTS = frozenset({"development", "test"})


class LocalProvider(WorkspaceProvider):
    name = "local"

    def __init__(self, settings: Any | None = None) -> None:
        from agentforge_shared.config import get_settings

        self.settings = settings or get_settings()
        if self.settings.environment not in NON_PRODUCTION_ENVIRONMENTS:
            raise ProviderError(
                "the local workspace provider has no isolation and is refused outside "
                f"development (AGENTFORGE_ENVIRONMENT={self.settings.environment!r}). "
                "Use kubernetes or podman."
            )
        self.root = Path(self.settings.local_workspace_root).expanduser()

    def _path(self, reference: str) -> Path:
        return self.root / reference

    def create(self, spec: WorkspaceSpec) -> WorkspaceState:
        path = self._path(spec.reference)
        path.mkdir(parents=True, exist_ok=True)

        if spec.repository_url and not (path / ".git").exists():
            if shutil.which("git"):
                result = subprocess.run(
                    [
                        "git",
                        "clone",
                        "--depth",
                        "20",
                        "--branch",
                        spec.revision,
                        spec.repository_url,
                        str(path),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode != 0:
                    log.warning("local git clone failed: %s", result.stderr.strip()[:300])
            else:
                log.warning("git is not on PATH; skipping clone for %s", spec.reference)

        if spec.secrets:
            log.warning(
                "local provider: refusing to inject %d secret(s) for %s. There is no "
                "isolated place to put them on the host.",
                len(spec.secrets),
                spec.reference,
            )

        state = self.get_status(spec.reference)
        state.detail["path"] = str(path)
        return state

    def start(self, reference: str) -> WorkspaceState:
        return self.get_status(reference)

    def stop(self, reference: str) -> None:
        log.info("local provider: stop is a no-op for %s", reference)

    def destroy(self, reference: str) -> None:
        path = self._path(reference)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    def exec(self, reference: str, command: list[str], *, container: str | None = None) -> str:
        path = self._path(reference)
        result = subprocess.run(command, cwd=path, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise ProviderError(
                f"command failed in {reference}: {result.stderr.strip()[:500]}",
                detail={"returncode": result.returncode},
            )
        return result.stdout

    def get_status(self, reference: str) -> WorkspaceState:
        path = self._path(reference)
        exists = path.is_dir()
        return WorkspaceState(
            reference=reference,
            provider=self.name,
            status=str(WorkspaceStatus.READY if exists else WorkspaceStatus.PENDING),
            ready=exists,
            detail={"path": str(path), "host_uid": os.getuid()},
        )

    def inject_secrets(self, reference: str, secrets: list[ResolvedSecret]) -> None:
        raise ProviderError(
            "the local provider cannot hold secrets: any file it wrote would be "
            "readable by the agent. Use kubernetes or podman."
        )

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "isolation": "none (development only)",
            "secrets": False,
            "network_policy": False,
            "exec": True,
            "persistent_volumes": False,
        }
