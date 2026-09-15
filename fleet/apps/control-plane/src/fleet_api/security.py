"""API authentication (SPEC §28).

One token for the prototype. It is intentionally a single dependency so that
project-scoped authorisation can replace it in one place.
"""

from __future__ import annotations

import logging

from fastapi import Header, HTTPException, status

from fleet_core.config import get_settings

log = logging.getLogger(__name__)

#: `/llm/v1/*` is called by workspaces, which hold a project token rather than
#: the operator token; those routes do their own check.
OPEN_PREFIXES = ("/llm/v1",)


def require_token(authorization: str = Header(default="")) -> str:
    settings = get_settings()
    if not settings.api_token:
        # No token configured means an open prototype; say so once, loudly,
        # rather than silently accepting anything.
        log.warning("FLEET_API_TOKEN is unset: the API is unauthenticated")
        return "anonymous"
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="expected an Authorization: Bearer <token> header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    import hmac

    if not hmac.compare_digest(token, settings.api_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
    return "operator"
