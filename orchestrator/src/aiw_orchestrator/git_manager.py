"""Git operations, always executed inside the workspace pod."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from aiw_workspace.controller import exec_in_workspace

log = logging.getLogger(__name__)


@dataclass(slots=True)
class BranchState:
    branch: str
    head: str
    clean: bool


class GitManager:
    def __init__(self, namespace: str, pod_name: str, workspace_path: str = "/workspace") -> None:
        self.namespace = namespace
        self.pod_name = pod_name
        self.path = workspace_path

    def _git(self, *args: str) -> str:
        return exec_in_workspace(self.namespace, self.pod_name, ["git", "-C", self.path, *args])

    def current_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def head(self) -> str:
        return self._git("rev-parse", "HEAD").strip()

    def ensure_branch(self, branch: str, base: str = "main") -> None:
        existing = self._git("branch", "--list", branch).strip()
        if existing:
            self._git("checkout", branch)
        else:
            self._git("checkout", "-b", branch, base)

    def diff(self, base: str) -> str:
        return self._git("diff", f"{base}...HEAD")

    def commit_all(self, message: str) -> str:
        self._git("add", "-A")
        # `commit` exits non-zero when there is nothing to commit; tolerate it.
        try:
            self._git("commit", "-m", message)
        except Exception as exc:  # noqa: BLE001
            log.info("nothing to commit or commit failed: %s", exc)
            return self.head()
        return self.head()

    def push(self, branch: str) -> None:
        self._git("push", "-u", "origin", branch)

    def state(self) -> BranchState:
        porcelain = self._git("status", "--porcelain").strip()
        return BranchState(branch=self.current_branch(), head=self.head(), clean=not porcelain)
