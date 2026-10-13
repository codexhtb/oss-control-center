"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from app.api.v1.router import router
from app.core.body_limit import LookupBodyLimit
from app.core.config import get_settings
from app.core.errors import ApiError, handle_api_error, handle_validation_error
from app.core.logging import configure_logging
from app.core.security import ensure_non_root_runtime
from app.db.engine import dispose_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    ensure_non_root_runtime()
    get_settings()
    try:
        yield
    finally:
        dispose_engine()


def create_app() -> FastAPI:
    configure_logging()
    application = FastAPI(title="OSS Control Center", lifespan=lifespan)

    @application.middleware("http")
    async def private_api_cache(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/") and not request.url.path.startswith(
            "/api/v1/health/"
        ):
            response.headers["Cache-Control"] = "no-store"
        return response

    application.add_middleware(LookupBodyLimit)
    application.add_exception_handler(ApiError, handle_api_error)
    application.add_exception_handler(RequestValidationError, handle_validation_error)
    application.include_router(router)
    return application


app = create_app()
