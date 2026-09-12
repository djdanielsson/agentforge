"""API key management. Issuing a key is itself privileged."""

from __future__ import annotations

from datetime import UTC, datetime

from agentforge_shared.models import ApiKey
from agentforge_shared.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyRead
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from ..deps import DbSession
from ..security import Principal, generate_key, require_write

router = APIRouter(prefix="/keys", tags=["auth"])


@router.get("", response_model=list[ApiKeyRead])
def list_keys(session: DbSession, principal: Principal = Depends(require_write)) -> list[ApiKey]:
    return list(session.scalars(select(ApiKey).order_by(ApiKey.created_at.desc())))


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_key(
    session: DbSession, payload: ApiKeyCreate, principal: Principal = Depends(require_write)
) -> ApiKey:
    plaintext, prefix, digest = generate_key()
    key = ApiKey(
        name=payload.name,
        prefix=prefix,
        key_hash=digest,
        scopes=payload.scopes,
        project_id=payload.project_id,
    )
    session.add(key)
    session.commit()
    session.refresh(key)
    # The only time the plaintext exists outside the caller's hands.
    return ApiKeyCreated(**ApiKeyRead.model_validate(key).model_dump(), key=plaintext)


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_key(
    session: DbSession, key_id: str, principal: Principal = Depends(require_write)
) -> None:
    key = session.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "key not found")
    key.active = False
    key.revoked_at = datetime.now(UTC)
    session.commit()
