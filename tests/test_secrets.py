"""Secret references: registration, scoping, and the guarantee that no value exists."""

from __future__ import annotations

API = "/api/v1"


def _project(client, name="Secretive"):
    return client.post(f"{API}/projects", json={"name": name}).json()


def test_a_secret_reference_has_no_value_field(client):
    """The schema is the guarantee: there is nowhere to put a secret."""
    project = _project(client)
    resp = client.post(
        f"{API}/projects/{project['id']}/secrets",
        json={
            "name": "github",
            "secret_name": "github-agent",
            "key": "token",
            "env_var": "GITHUB_TOKEN",
        },
    )
    assert resp.status_code == 201, resp.text
    ref = resp.json()
    assert ref["env_var"] == "GITHUB_TOKEN"
    assert ref["secret_name"] == "github-agent"
    assert ref["scope"] == "project"
    assert "value" not in ref
    assert "token" not in resp.text or "github-agent" in resp.text


def test_a_value_cannot_be_smuggled_in(client):
    project = _project(client)
    resp = client.post(
        f"{API}/projects/{project['id']}/secrets",
        json={
            "name": "sneaky",
            "secret_name": "s",
            "key": "k",
            "env_var": "TOKEN",
            "value": "ghp_should_be_ignored",
        },
    )
    # Pydantic ignores unknown fields rather than storing them; assert the
    # literal never reaches the response.
    assert resp.status_code == 201
    assert "ghp_should_be_ignored" not in resp.text


def test_env_var_must_look_like_an_env_var(client):
    project = _project(client)
    resp = client.post(
        f"{API}/projects/{project['id']}/secrets",
        json={"name": "bad", "secret_name": "s", "key": "k", "env_var": "not-an-env-var"},
    )
    assert resp.status_code == 422


def test_duplicate_names_in_the_same_scope_conflict(client):
    project = _project(client)
    body = {"name": "dup", "secret_name": "s", "key": "k", "env_var": "TOKEN"}
    assert client.post(f"{API}/projects/{project['id']}/secrets", json=body).status_code == 201
    assert client.post(f"{API}/projects/{project['id']}/secrets", json=body).status_code == 409


def test_project_secret_listing_includes_globals(client):
    project = _project(client, "Lister")
    client.post(
        f"{API}/secrets",
        json={
            "name": "gateway",
            "scope": "global",
            "secret_name": "llm",
            "key": "key",
            "env_var": "LLM_KEY",
        },
    )
    client.post(
        f"{API}/projects/{project['id']}/secrets",
        json={"name": "proj", "secret_name": "p", "key": "k", "env_var": "PROJ_KEY"},
    )

    listed = client.get(f"{API}/projects/{project['id']}/secrets").json()
    assert {entry["name"] for entry in listed} == {"gateway", "proj"}


def test_global_endpoint_refuses_project_scope(client):
    resp = client.post(
        f"{API}/secrets",
        json={"name": "x", "scope": "project", "secret_name": "s", "key": "k", "env_var": "X"},
    )
    assert resp.status_code == 400
    assert "global only" in resp.json()["detail"]


def test_agent_scoped_secret_requires_a_matching_agent(client):
    project = _project(client, "AgentScoped")
    other = _project(client, "Elsewhere")
    foreign_agent = client.post(
        f"{API}/projects/{other['id']}/agents", json={"name": "outsider"}
    ).json()

    resp = client.post(
        f"{API}/projects/{project['id']}/secrets",
        json={
            "name": "a",
            "scope": "agent",
            "agent_id": foreign_agent["id"],
            "secret_name": "s",
            "key": "k",
            "env_var": "A",
        },
    )
    assert resp.status_code == 400


def test_deleting_a_reference_leaves_the_real_secret_alone(client):
    project = _project(client, "Deleter")
    ref = client.post(
        f"{API}/projects/{project['id']}/secrets",
        json={"name": "temp", "secret_name": "s", "key": "k", "env_var": "T"},
    ).json()

    assert client.delete(f"{API}/secrets/{ref['id']}").status_code == 204
    assert client.get(f"{API}/projects/{project['id']}/secrets").json() == []


def test_resolution_respects_scope_boundaries(client):
    """A project must not see another project's secrets."""
    from agentforge_shared.db import session_scope
    from agentforge_shared.secrets import resolve_secrets

    first = _project(client, "First")
    second = _project(client, "Second")

    for project, env_var in ((first, "FIRST_KEY"), (second, "SECOND_KEY")):
        client.post(
            f"{API}/projects/{project['id']}/secrets",
            json={"name": env_var.lower(), "secret_name": "s", "key": "k", "env_var": env_var},
        )
    client.post(
        f"{API}/secrets",
        json={
            "name": "shared",
            "scope": "global",
            "secret_name": "g",
            "key": "k",
            "env_var": "SHARED_KEY",
        },
    )

    with session_scope() as session:
        resolved = resolve_secrets(session, project_id=first["id"])
    names = {secret.env_var for secret in resolved}
    assert names == {"FIRST_KEY", "SHARED_KEY"}
    assert "SECOND_KEY" not in names


def test_resolution_never_returns_a_value(client):
    from agentforge_shared.db import session_scope
    from agentforge_shared.secrets import resolve_secrets

    project = _project(client, "NoValues")
    client.post(
        f"{API}/projects/{project['id']}/secrets",
        json={"name": "k", "secret_name": "s", "key": "k", "env_var": "TOKEN"},
    )
    with session_scope() as session:
        resolved = resolve_secrets(session, project_id=project["id"])
    payload = resolved[0].model_dump()
    assert set(payload) == {"env_var", "secret_name", "key", "required"}
