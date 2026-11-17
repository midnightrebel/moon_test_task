import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status

from app.api.deps import SessionDep, verify_api_key
from app.api.schemas import PaymentAccepted, PaymentCreate, PaymentDetail
from app.services import payments as service

router = APIRouter(prefix="/api/v1/payments", tags=["payments"], dependencies=[Depends(verify_api_key)])


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PaymentAccepted,
    responses={409: {"description": "Idempotency-Key reused with a different request body"}},
)
async def create_payment(
    body: PaymentCreate,
    session: SessionDep,
    response: Response,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> PaymentAccepted:
    try:
        result = await service.create_payment(session, body, idempotency_key)
    except service.IdempotencyConflictError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Idempotency-Key has already been used with a different request body",
        ) from None

    if not result.created:
        response.headers["Idempotent-Replayed"] = "true"
    p = result.payment
    return PaymentAccepted(payment_id=p.id, status=p.status, created_at=p.created_at)


@router.get("/{payment_id}", response_model=PaymentDetail, responses={404: {"description": "Not found"}})
async def get_payment(payment_id: uuid.UUID, session: SessionDep) -> PaymentDetail:
    payment = await service.get_payment(session, payment_id)
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Payment not found")
    return PaymentDetail.model_validate(payment)
