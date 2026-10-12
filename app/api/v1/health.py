"""Liveness and dependency readiness checks."""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db.engine import get_engine

router = APIRouter(prefix="/health", tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/live")
def liveness() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/ready", response_model=None)
def readiness() -> dict[str, object] | JSONResponse:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, ValidationError, TimeoutError, OSError, ValueError) as exc:
        logger.warning("PostgreSQL readiness failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "dependencies": {"postgres": "down"}},
        )
    return {"status": "ready", "dependencies": {"postgres": "up"}}
