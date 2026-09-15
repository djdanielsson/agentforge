"""Configuration, read from the environment.

Everything the control plane needs to know about its surroundings lives here so
that the provider implementations never read `os.environ` themselves.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TAILNET = "tail7f3c08.ts.net"


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


@dataclass
class Settings:
    """Runtime settings.

    `devpod_enabled` defaults to false so that a deployment without the DevPod
    binary falls back to the native Kubernetes provider instead of failing every
    request. The value is recomputed from the filesystem, not assumed.
    """

    namespace: str = "fleet"
    data_dir: Path = Path("/data")
    database_url: str = ""
    api_token: str = ""

    # workspace provisioning
    workspace_provider: str = "devpod"
    devpod_binary: str = "devpod"
    devpod_home: Path = Path("/data/devpod")
    kubeconfig_path: Path = Path("/data/kubeconfig")
    workspace_image: str = "mcr.microsoft.com/devcontainers/base:ubuntu-24.04"
    workspace_cpu: str = "500m"
    workspace_memory: str = "1Gi"
    workspace_storage: str = "5Gi"
    workspace_storage_class: str = "local-path"
    namespace_prefix: str = "fleet-"
    #: Ports a workspace may reach on the public internet. 80 is not optional in
    #: practice: apt fetches from archive.ubuntu.com over HTTP, and a workspace
    #: that cannot install a package cannot build anything.
    workspace_egress_ports: list[int] = field(default_factory=lambda: [80, 443, 22])

    # LLM gateway
    llm_gateway_url: str = ""
    llm_gateway_key: str = ""
    llm_default_model: str = "local-coder"
    llm_models: list[str] = field(default_factory=lambda: ["local-coder", "fast", "smart"])
    llm_request_timeout: int = 180

    # integration surface
    control_plane_url: str = ""
    tailnet_domain: str = DEFAULT_TAILNET

    # agent tooling installed inside a workspace
    node_version: str = "v24.21.0"
    opencode_version: str = "1.18.31"
    t3_version: str = "0.0.40"
    t3_enabled: bool = True

    labels: dict[str, str] = field(
        default_factory=lambda: {"app.kubernetes.io/managed-by": "fleet-control-plane"}
    )

    @property
    def devpod_enabled(self) -> bool:
        return _bool("FLEET_DEVPOD_ENABLED", True)

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.environ.get("FLEET_DATA_DIR", "/data"))
        database_url = os.environ.get("FLEET_DATABASE_URL") or f"sqlite:///{data_dir}/fleet.db"
        return cls(
            namespace=os.environ.get("FLEET_NAMESPACE", "fleet"),
            data_dir=data_dir,
            database_url=database_url,
            api_token=os.environ.get("FLEET_API_TOKEN", ""),
            workspace_provider=os.environ.get("FLEET_WORKSPACE_PROVIDER", "devpod"),
            devpod_binary=os.environ.get("FLEET_DEVPOD_BINARY", "devpod"),
            devpod_home=Path(os.environ.get("FLEET_DEVPOD_HOME", str(data_dir / "devpod"))),
            kubeconfig_path=Path(os.environ.get("FLEET_KUBECONFIG", str(data_dir / "kubeconfig"))),
            workspace_image=os.environ.get(
                "FLEET_WORKSPACE_IMAGE", "mcr.microsoft.com/devcontainers/base:ubuntu-24.04"
            ),
            workspace_cpu=os.environ.get("FLEET_WORKSPACE_CPU", "500m"),
            workspace_memory=os.environ.get("FLEET_WORKSPACE_MEMORY", "1Gi"),
            workspace_storage=os.environ.get("FLEET_WORKSPACE_STORAGE", "5Gi"),
            workspace_storage_class=os.environ.get("FLEET_WORKSPACE_STORAGE_CLASS", "local-path"),
            namespace_prefix=os.environ.get("FLEET_NAMESPACE_PREFIX", "fleet-"),
            workspace_egress_ports=[
                int(p)
                for p in os.environ.get("FLEET_WORKSPACE_EGRESS_PORTS", "80,443,22").split(",")
                if p.strip()
            ],
            llm_gateway_url=os.environ.get("FLEET_LLM_GATEWAY_URL", ""),
            llm_gateway_key=os.environ.get("FLEET_LLM_GATEWAY_KEY", ""),
            llm_default_model=os.environ.get("FLEET_LLM_DEFAULT_MODEL", "local-coder"),
            llm_models=[
                m.strip()
                for m in os.environ.get("FLEET_LLM_MODELS", "local-coder,fast,smart").split(",")
                if m.strip()
            ],
            llm_request_timeout=_int("FLEET_LLM_REQUEST_TIMEOUT", 180),
            control_plane_url=os.environ.get("FLEET_CONTROL_PLANE_URL", ""),
            tailnet_domain=os.environ.get("FLEET_TAILNET_DOMAIN", DEFAULT_TAILNET),
            node_version=os.environ.get("FLEET_NODE_VERSION", "v24.21.0"),
            opencode_version=os.environ.get("FLEET_OPENCODE_VERSION", "1.18.31"),
            t3_version=os.environ.get("FLEET_T3_VERSION", "0.0.40"),
            t3_enabled=_bool("FLEET_T3_ENABLED", True),
        )


_settings: Settings | None = None


def get_settings(refresh: bool = False) -> Settings:
    global _settings
    if _settings is None or refresh:
        _settings = Settings.from_env()
    return _settings
