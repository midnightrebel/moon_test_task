from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange, RabbitQueue

PAYMENTS_NEW = "payments.new"
PAYMENTS_NEW_DLQ = "payments.new.dlq"

payments_exchange = RabbitExchange("payments", type=ExchangeType.DIRECT, durable=True)
retry_exchange = RabbitExchange("payments.retry", type=ExchangeType.DIRECT, durable=True)
dead_letter_exchange = RabbitExchange("payments.dlx", type=ExchangeType.DIRECT, durable=True)

payments_new_queue = RabbitQueue(
    PAYMENTS_NEW,
    durable=True,
    routing_key=PAYMENTS_NEW,
    arguments={
        "x-dead-letter-exchange": dead_letter_exchange.name,
        "x-dead-letter-routing-key": PAYMENTS_NEW_DLQ,
    },
)

dead_letter_queue = RabbitQueue(PAYMENTS_NEW_DLQ, durable=True, routing_key=PAYMENTS_NEW_DLQ)


def retry_queue_name(delay_ms: int) -> str:
    return f"{PAYMENTS_NEW}.retry.{delay_ms}ms"


def retry_queue(delay_ms: int) -> RabbitQueue:
    name = retry_queue_name(delay_ms)
    return RabbitQueue(
        name,
        durable=True,
        routing_key=name,
        arguments={
            "x-message-ttl": delay_ms,
            "x-dead-letter-exchange": payments_exchange.name,
            "x-dead-letter-routing-key": PAYMENTS_NEW,
        },
    )


async def declare_topology(broker: RabbitBroker, retry_delays_ms: list[int]) -> None:
    for exchange in (payments_exchange, retry_exchange, dead_letter_exchange):
        await broker.declare_exchange(exchange)

    bindings = [
        (payments_new_queue, payments_exchange),
        (dead_letter_queue, dead_letter_exchange),
        *((retry_queue(d), retry_exchange) for d in sorted(set(retry_delays_ms))),
    ]
    for queue, exchange in bindings:
        declared = await broker.declare_queue(queue)
        await declared.bind(exchange.name, routing_key=queue.routing())
