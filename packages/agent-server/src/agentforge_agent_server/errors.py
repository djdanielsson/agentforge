"""Agent Server error types.

The orchestrator needs to distinguish "the agent said no" from "the agent is not
there", because the first is a task outcome and the second is an infrastructure
problem with different retry semantics.
"""

from __future__ import annotations


class AgentServerError(RuntimeError):
    """The Agent Server answered, and the answer was an error."""

    def __init__(
        self, message: str, *, status_code: int | None = None, detail: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail

    @property
    def retryable(self) -> bool:
        """5xx and 429 are worth retrying; a 4xx is a bug in our request."""
        if self.status_code is None:
            return True
        return self.status_code >= 500 or self.status_code == 429


class AgentServerUnavailable(AgentServerError):
    """We could not reach the server at all: transport failure or timeout."""
