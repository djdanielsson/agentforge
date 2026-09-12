"""API key authentication and scoping."""

from __future__ import annotations

import contextlib

API = "/api/v1"


@contextlib.contextmanager
def auth_enabled():
    """Flip auth on for the duration of a test, then restore."""
    import os

    from agentforge_shared.config import get_settings

    previous = os.environ.get("AGENTFORGE_AUTH_ENABLED")
    os.environ["AGENTFORGE_AUTH_ENABLED"] = "true"
    get_settings.cache_clear()
    try:
        yield get_settings()
    finally:
        if previous is None:
            os.environ.pop("AGENTFORGE_AUTH_ENABLED", None)
        else:
            os.environ["AGENTFORGE_AUTH_ENABLED"] = previous
        get_settings.cache_clear()


def test_requests_are_rejected_without_a_key_when_auth_is_on(client):
    with auth_enabled():
        resp = client.get(f"{API}/projects")
        assert resp.status_code == 401
        assert "invalid API key" in resp.json()["detail"]


def test_a_minted_key_grants_access(client):
    with auth_enabled():
        # create the key while auth is off, then use it
        pass
    created = client.post(f"{API}/keys", json={"name": "ci"}).json()
    assert created["key"].startswith("af_")

    with auth_enabled():
        resp = client.get(f"{API}/projects", headers={"x-api-key": created["key"]})
        assert resp.status_code == 200, resp.text


def test_bearer_token_is_also_accepted(client):
    created = client.post(f"{API}/keys", json={"name": "bearer"}).json()

    with auth_enabled():
        resp = client.get(
            f"{API}/projects",
            headers={"authorization": f"Bearer {created['key']}"},
        )
        assert resp.status_code == 200, resp.text


def test_a_wrong_key_is_rejected(client):
    client.post(f"{API}/keys", json={"name": "real"})

    with auth_enabled():
        resp = client.get(f"{API}/projects", headers={"x-api-key": "af_deadbeef_nope"})
        assert resp.status_code == 401


def test_read_only_key_cannot_write(client):
    readonly = client.post(f"{API}/keys", json={"name": "ro", "scopes": ["read"]}).json()

    with auth_enabled():
        assert (
            client.get(f"{API}/projects", headers={"x-api-key": readonly["key"]}).status_code == 200
        )
        resp = client.post(
            f"{API}/projects",
            json={"name": "Nope"},
            headers={"x-api-key": readonly["key"]},
        )
        assert resp.status_code == 403
        assert "read-only" in resp.json()["detail"]


def test_revoking_a_key_locks_it_out(client):
    created = client.post(f"{API}/keys", json={"name": "temp"}).json()
    client.delete(f"{API}/keys/{created['id']}")

    with auth_enabled():
        resp = client.get(f"{API}/projects", headers={"x-api-key": created["key"]})
        assert resp.status_code == 401


def test_key_plaintext_is_never_listed(client):
    client.post(f"{API}/keys", json={"name": "hidden"})
    listed = client.get(f"{API}/keys").json()
    assert listed
    for entry in listed:
        assert "key" not in entry
        assert "key_hash" not in entry


def test_hashes_are_salted_by_content_not_stored_plaintext(client):
    """The plaintext must not be recoverable from the stored row."""
    from agentforge_shared.db import session_scope
    from agentforge_shared.models import ApiKey
    from sqlalchemy import select

    created = client.post(f"{API}/keys", json={"name": "hashed"}).json()

    with session_scope() as session:
        row = session.scalars(select(ApiKey).where(ApiKey.name == "hashed")).first()
        assert row is not None
        assert row.key_hash != created["key"]
        assert created["key"] not in row.key_hash


def test_project_pinned_key_cannot_reach_other_projects(client):
    first = client.post(f"{API}/projects", json={"name": "Pinned"}).json()
    second = client.post(f"{API}/projects", json={"name": "Other"}).json()
    pinned = client.post(f"{API}/keys", json={"name": "pinned", "project_id": first["id"]}).json()
    assert pinned["project_id"] == first["id"]

    with auth_enabled():
        headers = {"x-api-key": pinned["key"]}

        # its own project: fine
        assert client.get(f"{API}/projects/{first['id']}", headers=headers).status_code == 200

        # another project: indistinguishable from missing
        assert client.get(f"{API}/projects/{second['id']}", headers=headers).status_code == 404
        assert (
            client.post(
                f"{API}/projects/{second['id']}/tasks", json={"prompt": "sneak"}, headers=headers
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"{API}/projects/{second['id']}/review", json={}, headers=headers
            ).status_code
            == 404
        )

        # the list is filtered down to the pinned project
        listed = client.get(f"{API}/projects", headers=headers).json()
        assert [p["id"] for p in listed] == [first["id"]]


def test_anonymous_principal_has_full_scope_when_auth_is_off(client):
    from agentforge_api.security import Principal

    principal = Principal()
    assert principal.name == "anonymous"
    assert principal.can("write")
    assert principal.may_touch_project("anything")
