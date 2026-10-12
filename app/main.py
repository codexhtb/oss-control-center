"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.router import router
from app.core.logging import configure_logging
from app.core.security import ensure_non_root_runtime
from app.db.engine import dispose_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    ensure_non_root_runtime()
    try:
        yield
    finally:
        dispose_engine()


def create_app() -> FastAPI:
    configure_logging()
    application = FastAPI(title="OSS Control Center", lifespan=lifespan)
    application.include_router(router)
    return application


app = create_app()
