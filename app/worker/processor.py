import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Payment, PaymentStatus
from app.worker.gateway import PaymentGatewayEmulator
from app.worker.webhook import WebhookNotifier

logger = logging.getLogger(__name__)


class NonRetryableError(Exception):
    pass


class PaymentNotFoundError(NonRetryableError):
    pass


class PaymentProcessor:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        gateway: PaymentGatewayEmulator,
        notifier: WebhookNotifier,
    ) -> None:
        self._session_factory = session_factory
        self._gateway = gateway
        self._notifier = notifier

    async def process(self, payment_id: uuid.UUID) -> None:
        payment = await self._charge_if_pending(payment_id)
        if payment.webhook_delivered_at is not None:
            logger.info("Payment %s: webhook already delivered, nothing to do", payment_id)
            return
        await self._notifier.notify(payment)
        await self._mark_webhook_delivered(payment_id)

    async def _charge_if_pending(self, payment_id: uuid.UUID) -> Payment:
        async with self._session_factory() as session, session.begin():
            payment = await session.get(Payment, payment_id, with_for_update=True)
            if payment is None:
                raise PaymentNotFoundError(f"payment {payment_id} not found")

            if payment.status is PaymentStatus.PENDING:
                payment.status = await self._gateway.charge(payment.id)
                payment.processed_at = datetime.now(UTC)
                logger.info("Payment %s processed: %s", payment_id, payment.status.value)
            else:
                logger.info("Payment %s already %s, skipping gateway", payment_id, payment.status.value)
        return payment

    async def _mark_webhook_delivered(self, payment_id: uuid.UUID) -> None:
        async with self._session_factory() as session, session.begin():
            await session.execute(
                update(Payment)
                .where(Payment.id == payment_id, Payment.webhook_delivered_at.is_(None))
                .values(webhook_delivered_at=datetime.now(UTC))
            )
