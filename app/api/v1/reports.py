"""Read-only Service Desk reporting endpoints."""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel

from app.api.v1.identity import CurrentIdentityDep, SessionDep
from app.core.errors import ApiError
from app.modules.identity.access import has_permission
from app.modules.identity.roles import Permission
from app.modules.reports.service import report

router = APIRouter(prefix="/reports", tags=["reports"])


class Metrics(BaseModel):
    created: int
    resolved_events: int
    closed_events: int
    returned_events: int
    active_now: int
    average_resolution_seconds: float | None
    average_closure_seconds: float | None


class DailyPoint(BaseModel):
    date: date
    created: int
    closed: int


class Distribution(BaseModel):
    types: dict[str, int]
    statuses: dict[str, int]
    priorities: dict[str, int]


class Detail(BaseModel):
    id: UUID
    number: int
    subject: str | None
    ticket_type: str
    status: str
    priority: str
    created_at: datetime
    assignee_name: str | None


class DetailPage(BaseModel):
    items: list[Detail]
    page: int
    size: int
    total: int


class GeneralReport(BaseModel):
    start: date
    end: date
    metrics: Metrics
    daily: list[DailyPoint]
    distribution: Distribution
    details: DetailPage


@router.get("/general", response_model=GeneralReport)
def general_report(
    start: date,
    end: date,
    identity: CurrentIdentityDep,
    db: SessionDep,
    response: Response,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> GeneralReport:
    if not has_permission(db, identity.user, Permission.REPORTS_READ):
        raise ApiError(403, "forbidden", "Permission denied")
    all_access = has_permission(db, identity.user, Permission.TICKET_READ)
    if not all_access and not has_permission(db, identity.user, Permission.TICKET_READ_OWN):
        raise ApiError(403, "forbidden", "Permission denied")
    if start == date.min or end == date.max or end < start or (end - start).days > 3660:
        raise ApiError(422, "invalid_date_range", "Invalid report period")
    response.headers["Cache-Control"] = "no-store"
    return GeneralReport.model_validate(
        report(
            db, start=start, end=end, user=identity.user, read_all=all_access, page=page, size=size
        )
    )
