"""Process-wide settings, loaded from environment (and .env in dev)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENTFORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- core ---
    environment: str = "development"
    log_level: str = "INFO"

    # --- database ---
    database_url: str = "sqlite+pysqlite:///./agentforge.db"

    # --- api ---
    api_host: str = "0.0.0.0"
    # Deliberately not called `api_port`: Kubernetes injects
    # `<SERVICE_NAME>_PORT` for every Service, so a Service named
    # `agentforge-api` sets AGENTFORGE_API_PORT to a value like
    # "tcp://10.43.x.y:8000" and pydantic would refuse to parse it.
    http_port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- workspaces ---
    #: kubernetes (reference) | podman (local dev) | local (no isolation, dev only)
    workspace_provider: str = "kubernetes"
    #: Where the local provider puts workspaces. Ignored by other providers.
    local_workspace_root: str = "~/.agentforge/workspaces"
    k8s_in_cluster: bool = False
    kubeconfig: str | None = None
    workspace_namespace_prefix: str = "af"
    workspace_image: str = "ghcr.io/coder/code-server:latest"
    workspace_agent_image: str = "ghcr.io/all-hands-ai/openhands:latest"
    #: The uid/gid the agent runtime image expects. Defaults match the
    #: OpenHands image's `openhands` user. It must be the image's own user:
    #: the entrypoint is mode 770 owned by it, so neither uid 0 (without
    #: CAP_DAC_OVERRIDE) nor any other uid can execute it.
    workspace_agent_uid: int = 42420
    workspace_agent_gid: int = 42420
    workspace_cpu_request: str = "500m"
    workspace_memory_request: str = "1Gi"
    workspace_storage: str = "10Gi"
    workspace_storage_class: str | None = None
    workspace_code_server_port: int = 8080

    # --- agents ---
    openhands_url: str = "http://openhands:3000"
    #: Optional bearer token for the Agent Server API.
    agent_server_api_key: str | None = None
    llm_gateway_url: str = "http://litellm:4000"
    default_agent_model: str = "local-coder"

    # --- auth ---
    #: Dev convenience: with auth off, the API is open and the CLI needs no key.
    #: Any real deployment must set this true.
    auth_enabled: bool = False
    #: If set, this key is created on first boot so automation has a way in.
    bootstrap_api_key: str | None = None

    # --- webhooks ---
    webhook_timeout_seconds: float = 15.0
    webhook_max_attempts: int = 5
    webhook_retry_backoff_seconds: int = 30
    #: Refuse to deliver to these prefixes unless explicitly overridden. Webhook
    #: targets are attacker-controlled URLs, so SSRF protection is not optional.
    webhook_deny_hosts: list[str] = Field(
        default_factory=lambda: ["169.254.169.254", "metadata.google.internal"]
    )

    # --- orchestrator ---
    orchestrator_poll_interval: float = 2.0
    task_lease_seconds: int = 300
    task_max_attempts: int = 3

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()
