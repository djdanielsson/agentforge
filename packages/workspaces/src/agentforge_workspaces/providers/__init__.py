"""Workspace providers.

A provider is the only thing that knows how to turn a WorkspaceSpec into a real,
isolated place to run an agent. Kubernetes is the reference implementation;
Podman exists so the same project definition can be developed locally.
"""

from .base import (
    ProviderError,
    WorkspaceProvider,
    WorkspaceSpec,
    WorkspaceState,
)
from .registry import available_providers, get_provider

__all__ = [
    "ProviderError",
    "WorkspaceProvider",
    "WorkspaceSpec",
    "WorkspaceState",
    "available_providers",
    "get_provider",
]
