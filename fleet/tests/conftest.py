"""Test fixtures.

The suite runs entirely offline: no cluster, no DevPod binary, no LLM gateway.
What it *does* exercise is every interface the cluster deployment relies on.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "core" / "src"))
sys.path.insert(0, str(ROOT / "apps" / "control-plane" / "src"))
sys.path.insert(0, str(ROOT / "tests"))


@pytest.fixture(scope="session", autouse=True)
def environment() -> None:
    tmp = tempfile.mkdtemp(prefix="fleet-tests-")
    os.environ.update(
        {
            "FLEET_DATA_DIR": tmp,
            "FLEET_DATABASE_URL": f"sqlite:///{tmp}/fleet.db",
            "FLEET_NAMESPACE": "fleet-test",
            "FLEET_API_TOKEN": "test-token",
            "FLEET_WORKSPACE_PROVIDER": "fake",
            "FLEET_LLM_GATEWAY_URL": "http://gateway.invalid:4000",
            "FLEET_LLM_GATEWAY_KEY": "",
            "FLEET_TOKEN_SECRET": "test-secret",
            "FLEET_T3_ENABLED": "false",
        }
    )


@pytest.fixture(scope="session", autouse=True)
def no_real_cluster():
    """Stop the suite from reaching a real API server.

    Only the workspace providers and the credential store talk to Kubernetes,
    and both go through one accessor, so this is a single seam rather than a
    blanket mock. Without it, a test run against a machine that happens to have
    cluster credentials would create real Secrets.
    """
    from fakes import FakeCluster
    from fleet_core.workspaces import kubernetes_common

    fake = FakeCluster()
    original = kubernetes_common.get_cluster
    kubernetes_common.get_cluster = lambda: fake
    yield fake
    kubernetes_common.get_cluster = original


@pytest.fixture()
def fake_provider(monkeypatch):
    from fakes import ExplodingWorkspaceProvider, FakeWorkspaceProvider
    from fleet_core import service
    from fleet_core.config import get_settings
    from fleet_core.workspaces import registry

    provider = FakeWorkspaceProvider()
    exploding = ExplodingWorkspaceProvider()

    def select(name=None, settings=None):
        return exploding if (name or "").lower() == "exploding" else provider

    # The registry is the only place a concrete provider is named, so patching
    # it here proves the rest of the system never reaches around the interface.
    monkeypatch.setitem(registry.WORKSPACE_PROVIDERS, "fake", FakeWorkspaceProvider)
    monkeypatch.setattr(registry, "get_workspace_provider", select)
    monkeypatch.setattr(service, "get_workspace_provider", select)

    from fleet_core.agents import opencode as opencode_module

    original_init = opencode_module.OpenCodeProvider.__init__

    def init(self, workspaces):
        original_init(self, workspaces)
        self.workspaces = provider

    monkeypatch.setattr(opencode_module.OpenCodeProvider, "__init__", init)
    get_settings(refresh=True)
    return provider


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from fleet_api.main import app
    from fleet_core.db import init_db

    init_db()
    with TestClient(app) as test_client:
        test_client.headers.update({"Authorization": "Bearer test-token"})
        yield test_client


@pytest.fixture()
def clean_db():
    """Empty the tables so each test starts from a known state."""
    from fleet_core.db import init_db, session_scope
    from fleet_core.models import (
        Agent,
        Credential,
        Event,
        Project,
        Task,
        UsageRecord,
        Workspace,
    )

    init_db()
    with session_scope() as session:
        for model in (UsageRecord, Event, Task, Agent, Workspace, Credential, Project):
            session.query(model).delete()
