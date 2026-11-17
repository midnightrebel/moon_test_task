import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from faststream import AckPolicy, FastStream
from faststream.rabbit import Channel, RabbitBroker
from faststream.rabbit.annotations import RabbitMessage

from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.logging_setup import setup_logging
from app.messaging.events import PaymentCreatedEvent
from app.messaging.topology import declare_topology, payments_exchange, payments_new_queue
from app.worker.gateway import PaymentGatewayEmulator
from app.worker.processor import PaymentProcessor
from app.worker.retry import RetryScheduler, current_attempt
from app.worker.webhook import WebhookNotifier

logger = logging.getLogger(__name__)
settings = get_settings()

broker = RabbitBroker(settings.rabbitmq_url, default_channel=Channel(prefetch_count=settings.consumer_prefetch))


class _Deps:
    processor: PaymentProcessor
    retry: RetryScheduler


deps = _Deps()


@asynccontextmanager
async def lifespan() -> AsyncIterator[None]:
    setup_logging(settings.log_level)
    engine = create_engine(settings.database_url, pool_size=settings.consumer_prefetch)
    http = httpx.AsyncClient(timeout=settings.webhook_timeout)

    deps.processor = PaymentProcessor(
        create_session_factory(engine),
        PaymentGatewayEmulator(
            min_delay=settings.gateway_min_delay,
            max_delay=settings.gateway_max_delay,
            success_rate=settings.gateway_success_rate,
        ),
        WebhookNotifier(http),
    )
    deps.retry = RetryScheduler(broker, max_attempts=settings.max_attempts, delays_ms=settings.retry_delays_ms)

    await broker.connect()
    await declare_topology(broker, settings.retry_delays_ms)
    try:
        yield
    finally:
        await http.aclose()
        await engine.dispose()


app = FastStream(broker, lifespan=lifespan)


@broker.subscriber(payments_new_queue, payments_exchange, ack_policy=AckPolicy.REJECT_ON_ERROR)
async def handle_payment_created(event: PaymentCreatedEvent, message: RabbitMessage) -> None:
    attempt = current_attempt(message.headers)
    try:
        await deps.processor.process(event.payment_id)
    except Exception as exc:
        await deps.retry.handle_failure(
            body=message.body, message_id=message.message_id, attempt=attempt, error=exc
        )
