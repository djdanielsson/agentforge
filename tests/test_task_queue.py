"""Queue semantics: leases, expiry and retry accounting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def _seed_task(status: str, *, attempts: int = 0, leased_until=None):
    from aiw_shared.db import session_scope
    from aiw_shared.models import Agent, Project, Task

    with session_scope() as session:
        project = Project(name="Q", slug=f"q-{datetime.now(UTC).timestamp()}", status="ready")
        session.add(project)
        session.flush()
        agent = Agent(project_id=project.id, name="worker", status="idle")
        session.add(agent)
        session.flush()
        task = Task(
            project_id=project.id,
            agent_id=agent.id,
            description="do the thing",
            status=status,
            attempts=attempts,
            leased_until=leased_until,
        )
        session.add(task)
        session.flush()
        return task.id, agent.id


def test_lease_claims_the_oldest_task(client):
    from aiw_orchestrator.task_queue import TaskQueue

    task_id, _ = _seed_task("queued")
    queue = TaskQueue("test-worker")

    leased = queue.lease()
    assert leased is not None
    assert leased.id == task_id
    assert leased.attempts == 1

    # a second lease finds nothing, because the first is held
    assert queue.lease() is None


def test_complete_clears_the_lease(client):
    from aiw_orchestrator.task_queue import TaskQueue

    task_id, _ = _seed_task("queued")
    queue = TaskQueue("test-worker")
    queue.lease()
    queue.complete(task_id, result="done", commits=["abc123"])

    task = queue.get(task_id)
    assert task.status == "succeeded"
    assert task.result == "done"
    assert task.commits == ["abc123"]
    assert task.leased_by is None
    assert task.completed_at is not None


def test_expired_lease_is_requeued(client):
    from aiw_orchestrator.task_queue import TaskQueue

    stale = datetime.now(UTC) - timedelta(seconds=10)
    task_id, _ = _seed_task("leased", attempts=1, leased_until=stale)

    queue = TaskQueue("test-worker")
    assert queue.requeue_expired() == 1
    assert queue.get(task_id).status == "queued"


def test_expired_lease_past_max_attempts_fails(client):
    from aiw_orchestrator.task_queue import TaskQueue

    stale = datetime.now(UTC) - timedelta(seconds=10)
    task_id, _ = _seed_task("leased", attempts=99, leased_until=stale)

    queue = TaskQueue("test-worker")
    queue.requeue_expired()
    task = queue.get(task_id)
    assert task.status == "failed"
    assert "max attempts" in task.error


def test_fail_records_the_error(client):
    from aiw_orchestrator.task_queue import TaskQueue

    task_id, _ = _seed_task("queued")
    queue = TaskQueue("test-worker")
    queue.lease()
    queue.fail(task_id, "agent exploded")

    task = queue.get(task_id)
    assert task.status == "failed"
    assert task.error == "agent exploded"
