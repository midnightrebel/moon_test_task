import uuid

from pydantic import BaseModel

PAYMENT_CREATED = "payment.created"


class PaymentCreatedEvent(BaseModel):
    payment_id: uuid.UUID
