"""Versioned API route aggregation."""

from fastapi import APIRouter

from app.api.v1.billing import router as billing_router
from app.api.v1.categories import router as categories_router
from app.api.v1.chat import router as chat_router
from app.api.v1.diagnostics import router as diagnostics_router
from app.api.v1.health import router as health_router
from app.api.v1.identity import router as identity_router
from app.api.v1.internal_tasks import router as internal_tasks_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.reports import router as reports_router
from app.api.v1.settings import router as settings_router
from app.api.v1.tickets import router as tickets_router

router = APIRouter(prefix="/api/v1")
router.include_router(health_router)
router.include_router(billing_router)
router.include_router(chat_router)
router.include_router(categories_router)
router.include_router(settings_router)
router.include_router(diagnostics_router)
router.include_router(identity_router)
router.include_router(internal_tasks_router)
router.include_router(notifications_router)
router.include_router(reports_router)
router.include_router(tickets_router)
