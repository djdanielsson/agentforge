"""Process-wide settings, loaded from environment (and .env in dev)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AIW_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- core ---
    environment: str = "development"
    log_level: str = "INFO"

    # --- database ---
    database_url: str = "sqlite+pysqlite:///./aiw.db"

    # --- api ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- workspaces (k8s) ---
    k8s_in_cluster: bool = False
    kubeconfig: str | None = None
    workspace_namespace_prefix: str = "aiw"
    workspace_image: str = "ghcr.io/coder/code-server:latest"
    workspace_cpu_request: str = "500m"
    workspace_memory_request: str = "1Gi"
    workspace_storage: str = "10Gi"
    workspace_storage_class: str | None = None
    workspace_code_server_port: int = 8080

    # --- agents ---
    openhands_url: str = "http://openhands:3000"
    llm_gateway_url: str = "http://litellm:4000"
    default_agent_model: str = "local-coder"

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
