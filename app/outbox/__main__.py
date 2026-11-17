import asyncio
import contextlib
import signal

from faststream.rabbit import RabbitBroker

from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.logging_setup import setup_logging
from app.messaging.topology import declare_topology
from app.outbox.relay import OutboxRelay


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    engine = create_engine(settings.database_url, pool_size=2)
    broker = RabbitBroker(settings.rabbitmq_url)
    await broker.start()
    await declare_topology(broker, settings.retry_delays_ms)

    relay = OutboxRelay(
        create_session_factory(engine),
        broker,
        batch_size=settings.outbox_batch_size,
        poll_interval=settings.outbox_poll_interval,
    )
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, relay.stop)

    try:
        await relay.run()
    finally:
        with contextlib.suppress(Exception):
            await broker.stop()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
