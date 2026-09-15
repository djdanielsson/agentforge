"""Provider registry.

The control plane asks for a provider by name; nothing else in the codebase
imports a concrete provider.
"""

from __future__ import annotations

import logging

from ..config import Settings, get_settings
from .base import ProviderError, WorkspaceProvider
from .devpod import DevPodKubernetesProvider
from .kubernetes import KubernetesWorkspaceProvider

log = logging.getLogger(__name__)

WORKSPACE_PROVIDERS: dict[str, type[WorkspaceProvider]] = {
    "devpod": DevPodKubernetesProvider,
    "kubernetes": KubernetesWorkspaceProvider,
}


def get_workspace_provider(
    name: str | None = None, settings: Settings | None = None
) -> WorkspaceProvider:
    """Resolve a provider, falling back rather than failing when requested one is absent.

    The requested provider is honoured unless its own `available()` says no, in
    which case the fallback is announced in the log and reported by
    `GET /api/v1/providers` rather than silently substituted.
    """
    settings = settings or get_settings()
    requested = (name or settings.workspace_provider or "devpod").strip().lower()

    cls = WORKSPACE_PROVIDERS.get(requested)
    if cls is None:
        raise ProviderError(
            f"unknown workspace provider {requested!r}; "
            f"known: {', '.join(sorted(WORKSPACE_PROVIDERS))}"
        )

    provider = cls(settings)
    available = getattr(provider, "available", None)
    if available is not None and not available()[0]:
        reason = available()[1]
        log.warning("workspace provider %s unavailable (%s); using kubernetes", requested, reason)
        return KubernetesWorkspaceProvider(settings)
    return provider


def describe_providers(settings: Settings | None = None) -> list[dict]:
    """Capability report for every known provider (SPEC §46).

    Reported by `GET /api/v1/providers` so a substitution — the registry falling
    back because DevPod is missing — is visible rather than silent.
    """
    settings = settings or get_settings()
    out = []
    for name, cls in WORKSPACE_PROVIDERS.items():
        try:
            out.append(
                {
                    "name": name,
                    "configured": settings.workspace_provider,
                    "selected": name == settings.workspace_provider,
                    "capabilities": cls(settings).capabilities(),
                }
            )
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            out.append({"name": name, "error": f"{type(exc).__name__}: {exc}"})
    return out
