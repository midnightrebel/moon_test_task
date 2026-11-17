from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://payments:payments@localhost:5432/payments"
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    api_key: str = Field(default="dev-secret-key", min_length=8)

    outbox_poll_interval: float = 1.0
    outbox_batch_size: int = 100

    consumer_prefetch: int = 10
    max_attempts: int = Field(default=3, ge=1)
    retry_base_delay: float = Field(default=2.0, gt=0)

    gateway_min_delay: float = 2.0
    gateway_max_delay: float = 5.0
    gateway_success_rate: float = Field(default=0.9, ge=0, le=1)

    webhook_timeout: float = 5.0

    log_level: str = "INFO"

    @property
    def retry_delays_ms(self) -> list[int]:
        return [int(self.retry_base_delay * 1000 * 2**i) for i in range(self.max_attempts - 1)]


@lru_cache
def get_settings() -> Settings:
    return Settings()
