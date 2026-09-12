"""Delivers events to registered webhooks.

Two stages, both idempotent, both driven off the database:

1. `enqueue()`   — find events that have no delivery row for an active webhook
                   whose patterns match, and create one.
2. `dispatch()`  — send every due delivery, sign it, and either mark it
                   delivered or schedule a retry with backoff.

State lives in `webhook_deliveries`, so a restart never loses or duplicates an
event: the unique (webhook_id, event_id) pair is the dedupe key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
from agentforge_shared.config import get_settings
from agentforge_shared.db import session_scope
from agentforge_shared.events import matches, serialise
from agentforge_shared.models import Event, Webhook, WebhookDelivery
from sqlalchemy import exists, select

log = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-AIWorkbench-Signature"
EVENT_HEADER = "X-AIWorkbench-Event"
USER_AGENT = "agentforge-webhooks/0.1"


def sign(secret: str, body: bytes) -> str:
    """HMAC-SHA256 over the exact bytes we send, so the receiver can verify us."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class WebhookDispatcher:
    def __init__(self, client: httpx.Client | None = None) -> None:
        self.settings = get_settings()
        self._client = client or httpx.Client(
            timeout=self.settings.webhook_timeout_seconds,
            headers={"user-agent": USER_AGENT},
        )

    def close(self) -> None:
        self._client.close()

    # --- stage 1: turn events into deliveries ------------------------------

    def enqueue(self, limit: int = 500) -> int:
        created = 0
        with session_scope() as session:
            webhooks = list(session.scalars(select(Webhook).where(Webhook.active.is_(True))))
            if not webhooks:
                return 0

            recent = list(
                session.scalars(select(Event).order_by(Event.created_at.desc()).limit(limit))
            )

            for webhook in webhooks:
                for event in recent:
                    if webhook.project_id and event.project_id != webhook.project_id:
                        continue
                    if not matches(event.type, webhook.events):
                        continue

                    already = session.scalar(
                        select(
                            exists().where(
                                WebhookDelivery.webhook_id == webhook.id,
                                WebhookDelivery.event_id == event.id,
                            )
                        )
                    )
                    if already:
                        continue

                    session.add(
                        WebhookDelivery(
                            webhook_id=webhook.id,
                            event_id=event.id,
                            status="pending",
                            next_attempt_at=datetime.now(UTC),
                        )
                    )
                    created += 1
        if created:
            log.info("queued %d webhook delivery(ies)", created)
        return created

    # --- stage 2: send them ------------------------------------------------

    def dispatch(self, limit: int = 50) -> int:
        now = datetime.now(UTC)
        sent = 0
        with session_scope() as session:
            due = list(
                session.scalars(
                    select(WebhookDelivery)
                    .where(
                        WebhookDelivery.status.in_(["pending", "failed"]),
                        WebhookDelivery.next_attempt_at <= now,
                    )
                    .order_by(WebhookDelivery.next_attempt_at)
                    .limit(limit)
                )
            )
            work = [
                (
                    delivery.id,
                    delivery.attempts,
                    session.get(Webhook, delivery.webhook_id),
                    session.get(Event, delivery.event_id),
                )
                for delivery in due
            ]

        for delivery_id, attempts, webhook, event in work:
            if webhook is None or event is None:
                self._finish(delivery_id, status="abandoned", error="webhook or event missing")
                continue
            sent += 1 if self._deliver(delivery_id, attempts, webhook, event) else 0
        return sent

    def _deliver(self, delivery_id: str, attempts: int, webhook: Webhook, event: Event) -> bool:
        body = json.dumps(
            {"event": serialise(event)}, separators=(",", ":"), sort_keys=True
        ).encode()
        headers = {
            "content-type": "application/json",
            SIGNATURE_HEADER: sign(webhook.secret, body),
            EVENT_HEADER: event.type,
        }

        try:
            response = self._client.post(webhook.url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            log.warning("webhook %s delivery failed: %s", webhook.id, exc)
            self._retry(delivery_id, attempts, error=str(exc)[:500])
            return False

        if 200 <= response.status_code < 300:
            self._finish(delivery_id, status="delivered", response_code=response.status_code)
            return True

        log.warning("webhook %s got HTTP %s", webhook.id, response.status_code)
        self._retry(
            delivery_id,
            attempts,
            error=f"HTTP {response.status_code}: {response.text[:300]}",
            response_code=response.status_code,
        )
        return False

    # --- bookkeeping -------------------------------------------------------

    def _retry(self, delivery_id: str, attempts: int, *, error: str,
               response_code: int | None = None) -> None:
        next_attempt = (attempts + 1) * self.settings.webhook_retry_backoff_seconds
        give_up = attempts + 1 >= self.settings.webhook_max_attempts
        with session_scope() as session:
            delivery = session.get(WebhookDelivery, delivery_id)
            if delivery is None:
                return
            delivery.attempts = attempts + 1
            delivery.error = error
            delivery.response_code = response_code
            if give_up:
                delivery.status = "failed"
                delivery.next_attempt_at = None
                log.error("giving up on delivery %s after %d attempts", delivery_id, attempts + 1)
            else:
                delivery.status = "pending"
                delivery.next_attempt_at = datetime.now(UTC) + timedelta(seconds=next_attempt)

    def _finish(self, delivery_id: str, *, status: str, response_code: int | None = None,
                error: str | None = None) -> None:
        with session_scope() as session:
            delivery = session.get(WebhookDelivery, delivery_id)
            if delivery is None:
                return
            delivery.status = status
            delivery.response_code = response_code
            delivery.error = error
            delivery.next_attempt_at = None
            if status == "delivered":
                delivery.delivered_at = datetime.now(UTC)

    # --- convenience -------------------------------------------------------

    def run_once(self) -> tuple[int, int]:
        queued = self.enqueue()
        sent = self.dispatch()
        return queued, sent
