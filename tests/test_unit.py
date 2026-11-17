from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.api.schemas import PaymentCreate
from app.config import Settings
from app.messaging.topology import PAYMENTS_NEW_DLQ
from app.services.payments import request_fingerprint
from app.worker.processor import PaymentNotFoundError
from app.worker.retry import ATTEMPT_HEADER, RetryScheduler, current_attempt


class FakeBroker:
    def __init__(self) -> None:
        self.published: list[dict] = []

    async def publish(self, body, **kwargs) -> None:
        self.published.append({"body": body, **kwargs})


def make_scheduler(max_attempts: int = 3, base: float = 2.0) -> tuple[RetryScheduler, FakeBroker]:
    broker = FakeBroker()
    settings = Settings(max_attempts=max_attempts, retry_base_delay=base)
    return RetryScheduler(broker, max_attempts=max_attempts, delays_ms=settings.retry_delays_ms), broker


def test_retry_delays_are_exponential() -> None:
    assert Settings(max_attempts=3, retry_base_delay=2).retry_delays_ms == [2000, 4000]
    assert Settings(max_attempts=5, retry_base_delay=0.5).retry_delays_ms == [500, 1000, 2000, 4000]
    assert Settings(max_attempts=1).retry_delays_ms == []


@pytest.mark.parametrize(
    ("headers", "expected"),
    [({}, 1), ({ATTEMPT_HEADER: 2}, 2), ({ATTEMPT_HEADER: "3"}, 3), ({ATTEMPT_HEADER: "x"}, 1), ({ATTEMPT_HEADER: 0}, 1)],
)
def test_current_attempt(headers: dict, expected: int) -> None:
    assert current_attempt(headers) == expected


@pytest.mark.parametrize(("attempt", "queue"), [(1, "payments.new.retry.2000ms"), (2, "payments.new.retry.4000ms")])
async def test_failed_attempt_goes_to_delay_queue(attempt: int, queue: str) -> None:
    scheduler, broker = make_scheduler()
    await scheduler.handle_failure(body=b"{}", message_id="m1", attempt=attempt, error=RuntimeError("boom"))

    [msg] = broker.published
    assert msg["exchange"].name == "payments.retry"
    assert msg["routing_key"] == queue
    assert msg["headers"][ATTEMPT_HEADER] == attempt + 1
    assert msg["message_id"] == "m1"
    assert msg["persist"] is True


async def test_last_attempt_goes_to_dlq() -> None:
    scheduler, broker = make_scheduler()
    await scheduler.handle_failure(body=b"{}", message_id="m1", attempt=3, error=RuntimeError("boom"))

    [msg] = broker.published
    assert msg["exchange"].name == "payments.dlx"
    assert msg["routing_key"] == PAYMENTS_NEW_DLQ
    assert "boom" in msg["headers"]["x-last-error"]


async def test_non_retryable_error_goes_to_dlq_immediately() -> None:
    scheduler, broker = make_scheduler()
    await scheduler.handle_failure(body=b"{}", message_id="m1", attempt=1, error=PaymentNotFoundError("gone"))
    assert broker.published[0]["routing_key"] == PAYMENTS_NEW_DLQ


def _payload(**overrides) -> dict:
    return {
        "amount": "100.50",
        "currency": "RUB",
        "description": "Order #1",
        "metadata": {"a": 1, "b": {"c": 2}},
        "webhook_url": "https://example.com/hook",
    } | overrides


def test_fingerprint_ignores_key_order_and_number_formatting() -> None:
    a = PaymentCreate.model_validate(_payload())
    b = PaymentCreate.model_validate(dict(reversed(_payload(amount="100.5", metadata={"b": {"c": 2}, "a": 1}).items())))
    assert request_fingerprint(a) == request_fingerprint(b)
    assert request_fingerprint(a) != request_fingerprint(PaymentCreate.model_validate(_payload(amount="100.51")))


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount": "0"},
        {"amount": "-1"},
        {"amount": "1.001"},
        {"currency": "GBP"},
        {"description": ""},
        {"webhook_url": "not-a-url"},
        {"unexpected": True},
    ],
)
def test_invalid_payment_rejected(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        PaymentCreate.model_validate(_payload(**overrides))


def test_valid_payment() -> None:
    p = PaymentCreate.model_validate(_payload())
    assert p.amount == Decimal("100.50")
    assert p.metadata == {"a": 1, "b": {"c": 2}}
