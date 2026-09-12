"""URL validation for webhook targets.

Webhook URLs are attacker-controlled outbound requests, which makes them a
server-side request forgery primitive. We refuse the obvious internal targets
unless the deployment explicitly opts in.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import HTTPException, status

ALLOWED_SCHEMES = {"http", "https"}


def _is_internal(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast


def validate_webhook_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"webhook scheme must be one of {sorted(ALLOWED_SCHEMES)}",
        )
    if not parsed.hostname:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "webhook url has no host")

    from agentforge_shared.config import get_settings

    settings = get_settings()
    if parsed.hostname in settings.webhook_deny_hosts:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "webhook host is not permitted")

    # Resolve and refuse internal addresses. Note this is best-effort: a DNS
    # record can change between this check and the request (TOCTOU), so a
    # production deployment should also pin egress at the network layer.
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or None)
    except socket.gaierror:
        return url  # unresolvable: allow registration, delivery will fail loudly

    for info in infos:
        if _is_internal(info[4][0]):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"webhook host resolves to a non-routable address ({info[4][0]})",
            )
    return url
