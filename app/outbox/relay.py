import asyncio
import logging
from datetime import UTC, datetime

from faststream.rabbit import RabbitBroker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import OutboxEvent

logger = logging.getLogger(__name__)


class OutboxRelay:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        broker: RabbitBroker,
        *,
        batch_size: int,
        poll_interval: float,
    ) -> None:
        self._session_factory = session_factory
        self._broker = broker
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._stopping = asyncio.Event()

    def stop(self) -> None:
        self._stopping.set()

    async def run(self) -> None:
        logger.info("Outbox relay started")
        while not self._stopping.is_set():
            try:
                published = await self.publish_batch()
            except Exception:
                logger.exception("Outbox relay iteration failed")
                published = 0
            if published < self._batch_size:
                try:
                    await asyncio.wait_for(self._stopping.wait(), timeout=self._poll_interval)
                except TimeoutError:
                    pass
        logger.info("Outbox relay stopped")

    async def publish_batch(self) -> int:
        published = 0
        async with self._session_factory() as session, session.begin():
            events = (
                await session.scalars(
                    select(OutboxEvent)
                    .where(OutboxEvent.published_at.is_(None))
                    .order_by(OutboxEvent.created_at)
                    .limit(self._batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()

            for event in events:
                try:
                    await self._broker.publish(
                        event.payload,
                        exchange=event.exchange,
                        routing_key=event.routing_key,
                        message_id=str(event.id),
                        message_type=event.event_type,
                        persist=True,
                    )
                except Exception as exc:
                    event.publish_attempts += 1
                    logger.warning("Failed to publish outbox event %s: %r", event.id, exc)
                    break
                event.published_at = datetime.now(UTC)
                event.publish_attempts += 1
                published += 1

        if published:
            logger.info("Published %d outbox event(s)", published)
        return published
