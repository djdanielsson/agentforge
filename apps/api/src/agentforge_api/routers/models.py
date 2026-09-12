"""The aliases an agent may run on.

The catalog itself lives in `agentforge_api.model_catalog`, because agent writes
need the same answer this endpoint serves.
"""

from __future__ import annotations

from agentforge_shared.config import get_settings
from fastapi import APIRouter

from ..model_catalog import models_and_source

router = APIRouter(prefix="/models", tags=["agents"])


@router.get("")
def list_models() -> dict:
    """Aliases the dashboard offers, plus the one a new agent gets by default."""
    settings = get_settings()
    models, source = models_and_source()
    return {"models": models, "source": source, "default": settings.default_agent_model}
