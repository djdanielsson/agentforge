"""Test bootstrap.

The shared settings and DB engine are built at import time, so the test database
must be pointed at a scratch file *before* anything imports aiw_shared.
"""

from __future__ import annotations

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="aiw-tests-")
os.environ["AIW_DATABASE_URL"] = f"sqlite+pysqlite:///{_TMP}/test.db"
os.environ["AIW_ENVIRONMENT"] = "test"
os.environ["AIW_LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402


@pytest.fixture()
def client():
    from aiw_api.main import app
    from aiw_shared.db import Base, engine
    from fastapi.testclient import TestClient

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with TestClient(app) as test_client:
        yield test_client
