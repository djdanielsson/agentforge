"""The orchestrator loop.

One process, three reconcilers, one queue. Deliberately simple: it is the thing
you debug at 2am, so it should have no magic.

    python -m aiw_orchestrator.main
"""

from __future__ import annotations

import logging
import signal
import sys
import time

from aiw_shared.config import get_settings
from aiw_shared.db import init_db

from .agent_manager import AgentManager
from .task_queue import TaskQueue
from .workspace_manager import WorkspaceManager

log = logging.getLogger("aiw.orchestrator")

_running = True


def _handle_signal(signum, _frame) -> None:
    global _running
    log.info("received signal %s, shutting down after this cycle", signum)
    _running = False


def cycle(queue: TaskQueue, workspaces: WorkspaceManager, agents: AgentManager) -> None:
    queue.requeue_expired()

    for workspace in workspaces.pending():
        log.info("provisioning workspace for project %s", workspace.project_id)
        workspaces.reconcile_one(workspace)

    workspaces.reconcile_readiness()

    task = queue.lease()
    if task is None:
        return

    agent = next((a for a in agents.runnable_agents() if a.id == task.agent_id), None)
    if agent is None:
        log.warning(
            "task %s references unknown/stopped agent %s; releasing", task.id, task.agent_id
        )
        queue._set_status(
            task.id, __import__("aiw_shared.enums", fromlist=["TaskStatus"]).TaskStatus.QUEUED
        )  # noqa: SLF001
        return

    queue.mark_running(task.id)
    agents.dispatch(agent, task)


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    init_db()
    queue = TaskQueue()
    workspaces = WorkspaceManager()
    agents = AgentManager()

    log.info(
        "orchestrator %s starting (poll %.1fs)", queue.name, settings.orchestrator_poll_interval
    )
    while _running:
        try:
            cycle(queue, workspaces, agents)
        except Exception:  # noqa: BLE001 - never let one bad cycle kill the loop
            log.exception("cycle failed")
        time.sleep(settings.orchestrator_poll_interval)

    log.info("orchestrator stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
