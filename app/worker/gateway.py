import asyncio
import random
import uuid

from app.db.models import PaymentStatus


class PaymentGatewayEmulator:
    def __init__(self, *, min_delay: float, max_delay: float, success_rate: float) -> None:
        self._min_delay = min_delay
        self._max_delay = max_delay
        self._success_rate = success_rate

    async def charge(self, payment_id: uuid.UUID) -> PaymentStatus:
        await asyncio.sleep(random.uniform(self._min_delay, self._max_delay))
        return PaymentStatus.SUCCEEDED if random.random() < self._success_rate else PaymentStatus.FAILED
