import logging

import httpx

from app.db.models import Payment

logger = logging.getLogger(__name__)


class WebhookDeliveryError(Exception):
    pass


def build_payload(payment: Payment) -> dict:
    return {
        "event": f"payment.{payment.status.value}",
        "payment_id": str(payment.id),
        "status": payment.status.value,
        "amount": str(payment.amount),
        "currency": payment.currency.value,
        "description": payment.description,
        "metadata": payment.meta,
        "idempotency_key": payment.idempotency_key,
        "created_at": payment.created_at.isoformat(),
        "processed_at": payment.processed_at.isoformat() if payment.processed_at else None,
    }


class WebhookNotifier:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def notify(self, payment: Payment) -> None:
        try:
            response = await self._client.post(
                payment.webhook_url,
                json=build_payload(payment),
                headers={"X-Event-Id": f"{payment.id}:{payment.status.value}"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise WebhookDeliveryError(f"webhook {payment.webhook_url} failed: {exc!r}") from exc
        logger.info("Webhook delivered for payment %s (%s)", payment.id, response.status_code)
