"""The aliases an agent may run on, and what they actually are.

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
    """The picker's options: an alias, and the model it resolves to."""
    settings = get_settings()
    models, source = models_and_source()
    return {
        "models": models,
        "names": [model["name"] for model in models],
        "source": source,
        "default": settings.default_agent_model,
    }
