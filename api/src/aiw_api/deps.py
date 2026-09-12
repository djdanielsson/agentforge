"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from aiw_shared.config import Settings, get_settings
from aiw_shared.db import get_session
from fastapi import Depends
from sqlalchemy.orm import Session

DbSession = Annotated[Session, Depends(get_session)]


def settings() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(settings)]

__all__ = ["DbSession", "SettingsDep", "get_session", "Iterator"]
