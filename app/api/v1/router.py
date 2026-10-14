"""Versioned API route aggregation."""

from fastapi import APIRouter

from app.api.v1.billing import router as billing_router
from app.api.v1.diagnostics import router as diagnostics_router
from app.api.v1.health import router as health_router
from app.api.v1.identity import router as identity_router
from app.api.v1.tickets import router as tickets_router

router = APIRouter(prefix="/api/v1")
router.include_router(health_router)
router.include_router(billing_router)
router.include_router(diagnostics_router)
router.include_router(identity_router)
router.include_router(tickets_router)
