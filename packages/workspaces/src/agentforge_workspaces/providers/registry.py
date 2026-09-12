"""Provider selection.

One process uses one provider, chosen by configuration. Keeping this a registry
rather than an if/else means adding a backend is a new module, not an edit to
the orchestrator.
"""

from __future__ import annotations

from functools import lru_cache

from .base import ProviderError, WorkspaceProvider

KNOWN = ("kubernetes", "podman", "local")


def _load(name: str) -> type[WorkspaceProvider]:
    # Imported lazily so a deployment that only uses Kubernetes does not need
    # the podman binary or vice versa.
    if name == "kubernetes":
        from .kubernetes import KubernetesProvider

        return KubernetesProvider
    if name == "podman":
        from .podman import PodmanProvider

        return PodmanProvider
    if name == "local":
        from .local import LocalProvider

        return LocalProvider
    raise ProviderError(f"unknown workspace provider {name!r}; known: {', '.join(KNOWN)}")


@lru_cache
def get_provider(name: str = "kubernetes") -> WorkspaceProvider:
    """Return a cached provider instance. Providers hold no per-workspace state."""
    return _load(name)()


def available_providers() -> list[str]:
    return list(KNOWN)
