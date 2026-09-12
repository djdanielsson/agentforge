"""Outbound event subscriptions.

This is how an external controller — Hermes, CI, a chat bot — stops polling and
starts reacting. Each delivery is signed with HMAC-SHA256 over the raw body so
the receiver can prove it came from us.
"""

from __future__ import annotations

import secrets

from agentforge_shared.models import Webhook, WebhookDelivery
from agentforge_shared.schemas import (
    WebhookCreate,
    WebhookCreated,
    WebhookDeliveryRead,
    WebhookRead,
    WebhookUpdate,
)
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from ..deps import DbSession
from ..security import Principal, require_write
from ..webhook_guard import validate_webhook_url

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("", response_model=list[WebhookRead])
def list_webhooks(session: DbSession, principal: Principal = Depends(require_write)):
    return list(session.scalars(select(Webhook).order_by(Webhook.created_at.desc())))


@router.post("", response_model=WebhookCreated, status_code=status.HTTP_201_CREATED)
def create_webhook(
    session: DbSession, payload: WebhookCreate, principal: Principal = Depends(require_write)
) -> Webhook:
    url = validate_webhook_url(payload.url)
    secret = payload.secret or f"whsec_{secrets.token_urlsafe(32)}"
    webhook = Webhook(
        url=url,
        description=payload.description,
        secret=secret,
        events=payload.events,
        project_id=payload.project_id,
    )
    session.add(webhook)
    session.commit()
    session.refresh(webhook)
    return WebhookCreated(**WebhookRead.model_validate(webhook).model_dump(), secret=secret)


@router.patch("/{webhook_id}", response_model=WebhookRead)
def update_webhook(
    session: DbSession,
    webhook_id: str,
    payload: WebhookUpdate,
    principal: Principal = Depends(require_write),
) -> Webhook:
    webhook = session.get(Webhook, webhook_id)
    if webhook is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "webhook not found")
    data = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "url" in data:
        data["url"] = validate_webhook_url(data["url"])
    for field, value in data.items():
        setattr(webhook, field, value)
    session.commit()
    session.refresh(webhook)
    return webhook


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_webhook(
    session: DbSession, webhook_id: str, principal: Principal = Depends(require_write)
) -> None:
    webhook = session.get(Webhook, webhook_id)
    if webhook is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "webhook not found")
    session.delete(webhook)
    session.commit()


@router.get("/{webhook_id}/deliveries", response_model=list[WebhookDeliveryRead])
def list_deliveries(
    session: DbSession, webhook_id: str, principal: Principal = Depends(require_write)
) -> list[WebhookDelivery]:
    """Delivery history — the first thing you want when a subscription looks quiet."""
    return list(
        session.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.webhook_id == webhook_id)
            .order_by(WebhookDelivery.created_at.desc())
            .limit(100)
        )
    )
