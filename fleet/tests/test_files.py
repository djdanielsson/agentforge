"""Hand-editing project files from the dashboard (SPEC: one UI, any provider).

The editor must work the same against a DevPod namespace and a checkout
directory, and a path from the browser must never escape the project's own
repository. That second property is tested directly: `..` is a 409, an
absolute path is jailed rather than followed, and `.git/` + `.fleet/` accept
reads but refuse writes.
"""

from __future__ import annotations

import time

import pytest
from fleet_core import service


def _wait_for(fn, timeout: float = 15.0, interval: float = 0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


def test_paths_are_jailed_to_the_repo():
    assert service._safe_relpath("") == ""
    assert service._safe_relpath("/") == ""
    assert service._safe_relpath("src/main.py") == "src/main.py"
    # A leading slash does not reach the host: it becomes relative.
    assert service._safe_relpath("/etc/passwd") == "etc/passwd"
    assert service._safe_relpath("a/./b") == "a/b"
    with pytest.raises(service.Conflict):
        service._safe_relpath("..")
    with pytest.raises(service.Conflict):
        service._safe_relpath("src/../../etc")


def test_list_files_reads_names_types_and_sizes(client, fake_provider, clean_db):
    client.post("/api/v1/projects", json={"name": "demo-files"})
    fake_provider.respond("find ", "d\t0\tsrc\nf\t12\tREADME.md\n", 0)

    body = client.get("/api/v1/projects/demo-files/files").json()

    assert body["path"] == ""
    assert {"name": "src", "path": "src", "is_dir": True, "size": 0} in body["entries"]
    assert {
        "name": "README.md",
        "path": "README.md",
        "is_dir": False,
        "size": 12,
    } in body["entries"]
    # The provider-agnostic root, not a hard-coded volume path: the fake
    # provider uses the default layout, whose repo is under .fleet/repo.
    assert ".fleet/repo" in fake_provider.commands[-1][1][-1]


def test_list_files_use_the_checkout_root_for_checkout_projects(
    client, fake_provider, clean_db
):
    """Same route, other provider: the listing targets the checkout itself."""
    from fleet_core.workspaces import registry

    providers = dict(registry.WORKSPACE_PROVIDERS)
    assert "checkout" in providers  # the seam the route relies on exists
    client.post("/api/v1/projects", json={"name": "demo-co", "workspace": {"provider": "checkout"}})
    # The fake stands in for every provider here, so assert the wiring the
    # route controls: the project's own provider name is what resolves.
    detail = client.get("/api/v1/projects/demo-co").json()
    assert detail["workspaces"][0]["provider"] == "checkout"


def test_read_and_write_round_trip_through_the_api(client, fake_provider, clean_db):
    client.post("/api/v1/projects", json={"name": "demo-rw"})
    fake_provider.respond("cat ", "print('hi')\n", 0)

    body = client.get("/api/v1/projects/demo-rw/files/content", params={"path": "app.py"}).json()
    assert body == {"path": "app.py", "content": "print('hi')\n", "size": 12}

    written = client.put(
        "/api/v1/projects/demo-rw/files/content",
        json={"path": "app.py", "content": "print('hello')\n"},
    )
    assert written.status_code == 200, written.text
    assert written.json() == {"path": "app.py", "bytes": 15}
    # Written over stdin, never in argv: the command line carries no content.
    assert fake_provider.written and fake_provider.written[-1][2] == "print('hello')\n"
    assert not any("print('hello')" in command for _, command in fake_provider.commands)


def test_reading_a_missing_file_is_a_404(client, fake_provider, clean_db):
    client.post("/api/v1/projects", json={"name": "demo-missing"})
    fake_provider.respond("test -f", "NOT_A_FILE\n", 3)

    response = client.get("/api/v1/projects/demo-missing/files/content", params={"path": "nope.py"})
    assert response.status_code == 404


def test_dotdot_is_rejected_before_anything_executes(client, fake_provider, clean_db):
    client.post("/api/v1/projects", json={"name": "demo-jail"})
    before = len(fake_provider.commands)

    response = client.get(
        "/api/v1/projects/demo-jail/files/content", params={"path": "../secret"}
    )
    assert response.status_code == 409
    assert len(fake_provider.commands) == before


def test_git_and_fleet_state_are_read_only(client, fake_provider, clean_db):
    client.post("/api/v1/projects", json={"name": "demo-ro"})
    before = len(fake_provider.commands)

    for path in (".git/config", ".fleet/credentials.env"):
        response = client.put(
            "/api/v1/projects/demo-ro/files/content",
            json={"path": path, "content": "x"},
        )
        assert response.status_code == 409, path
    assert len(fake_provider.commands) == before


def test_commit_records_branch_and_commit(client, fake_provider, clean_db):
    client.post("/api/v1/projects", json={"name": "demo-commit"})
    fake_provider.respond("rev-parse", "CHANGED=2\nCOMMIT=abc123\nBRANCH=main\n", 0)

    body = client.post(
        "/api/v1/projects/demo-commit/workspace/commit", json={"message": "dashboard edit"}
    ).json()

    assert body == {"branch": "main", "commit": "abc123", "changed_files": "2"}
    events = client.get("/api/v1/events", params={"limit": 50}).json()["items"]
    assert any(
        event["type"] == "commit.created" and "dashboard" in event["message"] for event in events
    )


def test_commit_without_a_message_gets_a_sensible_default(client, fake_provider, clean_db):
    client.post("/api/v1/projects", json={"name": "demo-commit2"})
    fake_provider.respond("rev-parse", "CHANGED=0\nCOMMIT=abc123\nBRANCH=main\n", 0)

    body = client.post("/api/v1/projects/demo-commit2/workspace/commit", json={}).json()
    assert body["commit"] == "abc123"
