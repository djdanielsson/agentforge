"""API keys, scopes and the request dependency.

The API is the control plane, so it is the thing worth attacking. Keys are
generated with `secrets`, only ever stored hashed, and can be scoped and pinned
to a single project — an automation credential should not be able to reach the
whole fleet.

When `AGENTFORGE_AUTH_ENABLED` is false (the development default) every request is
anonymous and unrestricted. Any real deployment sets it true.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from agentforge_shared.config import get_settings
from agentforge_shared.models import ApiKey
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .deps import get_session

log = logging.getLogger(__name__)

KEY_PREFIX = "af"
PREFIX_LENGTH = 8


@dataclass(slots=True)
class Principal:
    """Who is calling. `key` is None for anonymous development access."""

    key: ApiKey | None = None

    @property
    def name(self) -> str:
        return self.key.name if self.key else "anonymous"

    @property
    def scopes(self) -> list[str]:
        return list(self.key.scopes) if self.key else ["read", "write", "admin"]

    @property
    def project_id(self) -> str | None:
        return self.key.project_id if self.key else None

    def can(self, scope: str) -> bool:
        return scope in self.scopes or "admin" in self.scopes

    def may_touch_project(self, project_id: str) -> bool:
        """A project-pinned key may only act on its own project."""
        return self.project_id is None or self.project_id == project_id


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """Return (plaintext, prefix, hash). The plaintext is shown to the user once."""
    prefix = secrets.token_hex(4)
    body = secrets.token_urlsafe(32)
    plaintext = f"{KEY_PREFIX}_{prefix}_{body}"
    return plaintext, prefix, hash_key(plaintext)


def extract_token(request: Request) -> str | None:
    """Accept both `X-API-Key` and `Authorization: Bearer`."""
    header = request.headers.get("x-api-key")
    if header:
        return header.strip()
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def authenticate(session: Session, token: str | None) -> ApiKey | None:
    if not token or not token.startswith(f"{KEY_PREFIX}_"):
        return None
    prefix = token.split("_")[1] if len(token.split("_")) > 1 else ""
    candidate = session.scalars(
        select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.active.is_(True))
    ).first()
    if candidate is None:
        return None
    # constant-time comparison; the hash lookup above is by prefix, not secret
    if not hmac.compare_digest(candidate.key_hash, hash_key(token)):
        return None
    return candidate


def get_principal(request: Request, session: Session = Depends(get_session)) -> Principal:
    settings = get_settings()
    if not settings.auth_enabled:
        return Principal()

    token = extract_token(request)
    key = authenticate(session, token)
    if key is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing or invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )

    key.last_used_at = datetime.now(UTC)
    session.commit()
    return Principal(key=key)


def ensure_project_access(principal: Principal, project_id: str) -> None:
    """Enforce a project-pinned key.

    Called from every project-scoped funnel, so a credential minted for one
    project cannot read or write another. A 404 (not 403) is deliberate: a
    pinned key should not be able to probe which project ids exist.
    """
    if not principal.may_touch_project(project_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")


def require_write(principal: Principal = Depends(get_principal)) -> Principal:
    if not principal.can("write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "this key has read-only scope")
    return principal


def ensure_bootstrap_key(session: Session) -> str | None:
    """Create the configured bootstrap key once, so automation has a way in."""
    settings = get_settings()
    if not settings.auth_enabled or not settings.bootstrap_api_key:
        return None

    existing = session.scalars(select(ApiKey).where(ApiKey.name == "bootstrap")).first()
    if existing is not None:
        return None

    plaintext = settings.bootstrap_api_key
    session.add(
        ApiKey(
            name="bootstrap",
            prefix=plaintext.split("_")[1] if "_" in plaintext else "boot",
            key_hash=hash_key(plaintext),
            scopes=["read", "write", "admin"],
        )
    )
    session.commit()
    log.warning("created bootstrap API key from AGENTFORGE_BOOTSTRAP_API_KEY")
    return plaintext
