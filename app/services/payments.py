import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import PaymentCreate
from app.db.models import OutboxEvent, Payment
from app.messaging.events import PAYMENT_CREATED, PaymentCreatedEvent
from app.messaging.topology import PAYMENTS_NEW, payments_exchange


class IdempotencyConflictError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class CreateResult:
    payment: Payment
    created: bool


def request_fingerprint(data: PaymentCreate) -> str:
    canonical = json.dumps(data.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def create_payment(session: AsyncSession, data: PaymentCreate, idempotency_key: str) -> CreateResult:
    fingerprint = request_fingerprint(data)

    stmt = (
        insert(Payment)
        .values(
            id=uuid.uuid4(),
            amount=data.amount,
            currency=data.currency,
            description=data.description,
            meta=data.metadata,
            idempotency_key=idempotency_key,
            request_hash=fingerprint,
            webhook_url=str(data.webhook_url),
        )
        .on_conflict_do_nothing(index_elements=[Payment.idempotency_key])
        .returning(Payment)
    )
    payment = (await session.scalars(stmt)).one_or_none()

    if payment is None:
        await session.rollback()
        existing = await session.scalar(select(Payment).where(Payment.idempotency_key == idempotency_key))
        if existing is None:
            raise RuntimeError("idempotency key conflict but no payment found")
        if existing.request_hash != fingerprint:
            raise IdempotencyConflictError
        return CreateResult(existing, created=False)

    session.add(
        OutboxEvent(
            event_type=PAYMENT_CREATED,
            aggregate_id=payment.id,
            exchange=payments_exchange.name,
            routing_key=PAYMENTS_NEW,
            payload=PaymentCreatedEvent(payment_id=payment.id).model_dump(mode="json"),
        )
    )
    await session.commit()
    return CreateResult(payment, created=True)


async def get_payment(session: AsyncSession, payment_id: uuid.UUID) -> Payment | None:
    return await session.get(Payment, payment_id)
