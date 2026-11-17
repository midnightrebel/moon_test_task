import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from faststream.rabbit import RabbitBroker

from app.messaging.topology import (
    PAYMENTS_NEW_DLQ,
    dead_letter_exchange,
    retry_exchange,
    retry_queue_name,
)
from app.worker.processor import NonRetryableError

logger = logging.getLogger(__name__)

ATTEMPT_HEADER = "x-attempt"


def current_attempt(headers: Mapping[str, Any]) -> int:
    try:
        return max(1, int(headers.get(ATTEMPT_HEADER, 1)))
    except (TypeError, ValueError):
        return 1


class RetryScheduler:
    def __init__(self, broker: RabbitBroker, *, max_attempts: int, delays_ms: list[int]) -> None:
        if len(delays_ms) < max_attempts - 1:
            raise ValueError("need a retry delay for every attempt but the last")
        self._broker = broker
        self._max_attempts = max_attempts
        self._delays_ms = delays_ms

    async def handle_failure(
        self, *, body: bytes, message_id: str | None, attempt: int, error: Exception
    ) -> None:
        error_text = repr(error)[:1000]

        if isinstance(error, NonRetryableError) or attempt >= self._max_attempts:
            logger.error("Message %s -> DLQ after %d attempt(s): %s", message_id, attempt, error_text)
            await self._broker.publish(
                body,
                exchange=dead_letter_exchange,
                routing_key=PAYMENTS_NEW_DLQ,
                message_id=message_id,
                content_type="application/json",
                persist=True,
                headers={
                    ATTEMPT_HEADER: attempt,
                    "x-last-error": error_text,
                    "x-failed-at": datetime.now(UTC).isoformat(),
                },
            )
            return

        delay_ms = self._delays_ms[attempt - 1]
        logger.warning(
            "Message %s attempt %d/%d failed, retry in %.1fs: %s",
            message_id, attempt, self._max_attempts, delay_ms / 1000, error_text,
        )
        await self._broker.publish(
            body,
            exchange=retry_exchange,
            routing_key=retry_queue_name(delay_ms),
            message_id=message_id,
            content_type="application/json",
            persist=True,
            headers={ATTEMPT_HEADER: attempt + 1, "x-last-error": error_text},
        )
