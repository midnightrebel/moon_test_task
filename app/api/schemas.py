import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator

from app.db.models import Currency, PaymentStatus

Amount = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2, examples=["1500.00"])]


class PaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Amount
    currency: Currency
    description: str = Field(min_length=1, max_length=1024, examples=["Order #42"])
    metadata: dict[str, Any] = Field(default_factory=dict, examples=[{"order_id": 42}])
    webhook_url: AnyHttpUrl = Field(examples=["https://example.com/webhooks/payments"])

    @field_validator("amount")
    @classmethod
    def _normalize_amount(cls, v: Decimal) -> Decimal:
        return v.quantize(Decimal("0.01"))


class PaymentAccepted(BaseModel):
    payment_id: uuid.UUID
    status: PaymentStatus
    created_at: datetime


class PaymentDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    payment_id: uuid.UUID = Field(validation_alias="id")
    amount: Decimal
    currency: Currency
    description: str
    metadata: dict[str, Any] = Field(validation_alias="meta")
    status: PaymentStatus
    idempotency_key: str
    webhook_url: str
    created_at: datetime
    processed_at: datetime | None
    webhook_delivered_at: datetime | None
