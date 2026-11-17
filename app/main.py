from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.payments import router as payments_router
from app.config import get_settings
from app.db.session import create_engine, create_session_factory
from app.logging_setup import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    engine = create_engine(settings.database_url)
    app.state.session_factory = create_session_factory(engine)
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="Payment Processing Service", version="1.0.0", lifespan=lifespan)
    app.include_router(payments_router)
    return app


app = create_app()
