"""Client for the OpenHands Agent Server.

AgentForge does not embed an agent SDK. Each project workspace runs an Agent
Server, and this package is the only place that knows how to talk to it. That
keeps upstream API drift contained to one package.
"""

from .client import AgentServerClient, Routes
from .errors import AgentServerError, AgentServerUnavailable
from .models import AgentEvent, AgentRun, ConversationSpec, FileEntry

__all__ = [
    "AgentEvent",
    "AgentRun",
    "AgentServerClient",
    "AgentServerError",
    "AgentServerUnavailable",
    "ConversationSpec",
    "FileEntry",
    "Routes",
]
