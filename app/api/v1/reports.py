"""Read-only Service Desk reporting endpoints."""

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel

from app.api.v1.identity import CurrentIdentityDep, SessionDep
from app.core.errors import ApiError
from app.modules.identity.access import has_permission
from app.modules.identity.roles import Permission
from app.modules.reports.phase2b import field_work_report, reasons_report
from app.modules.reports.service import callbacks_report, employees_report, report

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


class EmployeeRow(BaseModel):
    user_id: UUID
    display_name: str
    role: str
    is_active: bool
    created_tickets: int
    decision_events: int
    callback_events: int
    callback_tickets: int
    outcomes: dict[str, int]


class EmployeeDay(BaseModel):
    user_id: UUID
    date: date
    created: int
    decisions: int
    callbacks: int


class EmployeesReport(BaseModel):
    start: date
    end: date
    items: list[EmployeeRow]
    daily: list[EmployeeDay]


class CallbackDay(BaseModel):
    date: date
    outcome: str
    count: int


class CallbackEmployee(BaseModel):
    user_id: UUID
    display_name: str
    outcome: str
    count: int


class CallbackDetail(BaseModel):
    id: UUID
    ticket_id: UUID
    ticket_number: int
    subject: str | None
    outcome: str
    occurred_at: datetime
    actor_id: UUID
    actor_name: str


class CallbackDetailsPage(BaseModel):
    items: list[CallbackDetail]
    page: int
    size: int
    total: int


class CallbacksReport(BaseModel):
    start: date
    end: date
    total_events: int
    unique_tickets: int
    outcomes: dict[str, int]
    daily: list[CallbackDay]
    employees: list[CallbackEmployee]
    details: CallbackDetailsPage


class ReportPage(BaseModel):
    items: list[dict]
    page: int
    size: int
    total: int


class FieldWorkReport(BaseModel):
    start: date
    end: date
    metrics: dict[str, int]
    directions: dict[str, dict[str, int]]
    daily: list[dict]
    reschedule_reasons: list[dict]
    details: ReportPage


class ReasonsReport(BaseModel):
    start: date
    end: date
    metrics: dict[str, int]
    categories: list[dict]
    reasons: list[dict]
    daily: list[dict]
    details: ReportPage


def _check_access(identity: CurrentIdentityDep, db: SessionDep, start: date, end: date) -> bool:
    if not has_permission(db, identity.user, Permission.REPORTS_READ):
        raise ApiError(403, "forbidden", "Permission denied")
    all_access = has_permission(db, identity.user, Permission.TICKET_READ)
    if not all_access and not has_permission(db, identity.user, Permission.TICKET_READ_OWN):
        raise ApiError(403, "forbidden", "Permission denied")
    if start == date.min or end == date.max or end < start or (end - start).days > 3660:
        raise ApiError(422, "invalid_date_range", "Invalid report period")
    return all_access


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
    all_access = _check_access(identity, db, start, end)
    response.headers["Cache-Control"] = "no-store"
    return GeneralReport.model_validate(
        report(
            db, start=start, end=end, user=identity.user, read_all=all_access, page=page, size=size
        )
    )


@router.get("/employees", response_model=EmployeesReport)
def employee_activity(
    start: date, end: date, identity: CurrentIdentityDep, db: SessionDep, response: Response
) -> EmployeesReport:
    all_access = _check_access(identity, db, start, end)
    response.headers["Cache-Control"] = "no-store"
    return EmployeesReport.model_validate(
        employees_report(db, start=start, end=end, user=identity.user, read_all=all_access)
    )


@router.get("/callbacks", response_model=CallbacksReport)
def callback_activity(
    start: date,
    end: date,
    identity: CurrentIdentityDep,
    db: SessionDep,
    response: Response,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> CallbacksReport:
    all_access = _check_access(identity, db, start, end)
    response.headers["Cache-Control"] = "no-store"
    return CallbacksReport.model_validate(
        callbacks_report(
            db,
            start=start,
            end=end,
            user=identity.user,
            read_all=all_access,
            page=page,
            size=size,
        )
    )


@router.get("/field-work", response_model=FieldWorkReport)
def field_work_activity(
    start: date,
    end: date,
    identity: CurrentIdentityDep,
    db: SessionDep,
    response: Response,
    direction: Literal["SUKHUM", "OCHAMCHIRA"] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> FieldWorkReport:
    all_access = _check_access(identity, db, start, end)
    response.headers["Cache-Control"] = "no-store"
    return FieldWorkReport.model_validate(
        field_work_report(
            db,
            start=start,
            end=end,
            user=identity.user,
            read_all=all_access,
            direction=direction,
            page=page,
            size=size,
        )
    )


@router.get("/reasons", response_model=ReasonsReport)
def ticket_reasons(
    start: date,
    end: date,
    identity: CurrentIdentityDep,
    db: SessionDep,
    response: Response,
    category: Annotated[str | None, Query(max_length=160)] = None,
    detail_kind: Literal["category", "decision"] = "category",
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ReasonsReport:
    all_access = _check_access(identity, db, start, end)
    response.headers["Cache-Control"] = "no-store"
    return ReasonsReport.model_validate(
        reasons_report(
            db,
            start=start,
            end=end,
            user=identity.user,
            read_all=all_access,
            category=category,
            detail_kind=detail_kind,
            page=page,
            size=size,
        )
    )
