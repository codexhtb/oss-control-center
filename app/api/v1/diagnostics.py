"""Explicit, CSRF-protected monitoring checks and engineer confirmation."""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.v1.identity import CsrfIdentityDep, CurrentIdentityDep, SessionDep
from app.core.errors import ApiError
from app.modules.diagnostics.config import MappingRegistry
from app.modules.diagnostics.correlation import CorrelationEngine, DiagnosticReport
from app.modules.diagnostics.factory import get_correlation_engine, get_onu_runtime
from app.modules.diagnostics.onu import BdcomOnuReader, OnuResult
from app.modules.diagnostics.onu_correlation import lookup_onu_candidates
from app.modules.identity.models import User
from app.modules.identity.roles import Permission, Role, permissions_for
from app.modules.tickets.models import (
    ConnectionSnapshot,
    DiagnosticCheck,
    SubscriberSnapshot,
    Ticket,
)
from app.modules.tickets.schemas import DiagnosticView
from app.modules.tickets.service import confirm_monitoring_check

router = APIRouter(tags=["diagnostics"])


class LookupCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connection_snapshot_id: UUID


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_id: UUID


class TicketCheckResult(BaseModel):
    check_id: UUID
    expires_at: datetime
    report: DiagnosticReport


def engine_dependency() -> CorrelationEngine:
    try:
        return get_correlation_engine()
    except (ValueError, OSError, TypeError):
        raise ApiError(
            503, "monitoring_configuration_unavailable", "Monitoring configuration unavailable"
        ) from None


EngineDep = Annotated[CorrelationEngine, Depends(engine_dependency)]
IfMatch = Annotated[str | None, Header(alias="If-Match")]


def _engineer(identity: CsrfIdentityDep) -> UUID:
    if Permission.DIAGNOSTICS_RUN not in permissions_for(Role(identity.user.role)):
        raise ApiError(403, "forbidden", "Permission denied")
    return identity.user.id


def onu_dependency() -> tuple[MappingRegistry, BdcomOnuReader | None]:
    try:
        return get_onu_runtime()
    except (ValueError, OSError, TypeError):
        raise ApiError(
            503, "monitoring_configuration_unavailable", "Monitoring configuration unavailable"
        ) from None


OnuRuntimeDep = Annotated[tuple[MappingRegistry, BdcomOnuReader | None], Depends(onu_dependency)]


def _still_engineer(db: SessionDep, actor_id: UUID) -> None:
    user = db.execute(
        select(User)
        .where(User.id == actor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        not user
        or not user.is_active
        or Permission.DIAGNOSTICS_RUN not in permissions_for(Role(user.role))
    ):
        raise ApiError(403, "forbidden", "Permission denied")


def _version(raw: str | None) -> int:
    if raw is None:
        raise ApiError(428, "version_required", "If-Match ticket version is required")
    value = raw.strip('"')
    if not value.isdecimal() or int(value) < 1:
        raise ApiError(400, "invalid_if_match", "If-Match must be a ticket version")
    return int(value)


@router.post("/subscriber-lookups/{lookup_id}/diagnostics/check", response_model=DiagnosticReport)
def check_lookup(
    lookup_id: UUID,
    payload: LookupCheckRequest,
    response: Response,
    identity: CsrfIdentityDep,
    db: SessionDep,
    engine: EngineDep,
) -> DiagnosticReport:
    actor_id = _engineer(identity)
    snapshot = db.get(SubscriberSnapshot, lookup_id)
    if snapshot is None or snapshot.requested_by != actor_id:
        raise ApiError(404, "lookup_not_found", "Subscriber lookup not found")
    connection = db.get(ConnectionSnapshot, payload.connection_snapshot_id)
    if connection is None or connection.subscriber_snapshot_id != lookup_id:
        raise ApiError(422, "invalid_connection", "Connection is not in subscriber lookup")
    db.expunge(connection)
    db.rollback()  # Monitoring calls never hold a PostgreSQL transaction.
    report = engine.check(connection)
    _still_engineer(db, actor_id)
    response.headers["Cache-Control"] = "no-store"
    return report


@router.post("/tickets/{ticket_id}/diagnostics/check", response_model=TicketCheckResult)
def check_ticket(
    ticket_id: UUID,
    response: Response,
    identity: CsrfIdentityDep,
    db: SessionDep,
    engine: EngineDep,
    if_match: IfMatch = None,
) -> TicketCheckResult:
    actor_id = _engineer(identity)
    expected = _version(if_match)
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise ApiError(404, "ticket_not_found", "Ticket not found")
    if ticket.subscriber_snapshot_id is None or ticket.connection_snapshot_id is None:
        raise ApiError(409, "connection_required", "Ticket has no confirmed connection")
    if ticket.status == "CLOSED":
        raise ApiError(409, "ticket_closed", "Closed ticket cannot be diagnosed")
    if ticket.version != expected:
        raise ApiError(409, "version_conflict", "Ticket was changed; reload it")
    connection = db.get(ConnectionSnapshot, ticket.connection_snapshot_id)
    if connection is None:
        raise ApiError(409, "connection_missing", "Confirmed connection is unavailable")
    connection_id = connection.id
    subscriber_id = ticket.subscriber_snapshot_id
    db.expunge(connection)
    db.rollback()
    report = engine.check(connection)
    _still_engineer(db, actor_id)
    current = db.execute(
        select(Ticket)
        .where(Ticket.id == ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        current is None
        or current.version != expected
        or current.status == "CLOSED"
        or current.connection_snapshot_id != connection_id
    ):
        raise ApiError(409, "version_conflict", "Ticket was changed; reload it")
    expires = datetime.now(UTC) + timedelta(minutes=15)
    check = DiagnosticCheck(
        ticket_id=ticket_id,
        subscriber_snapshot_id=subscriber_id,
        connection_snapshot_id=connection_id,
        requested_by=actor_id,
        report=report.model_dump(mode="json"),
        expires_at=expires,
    )
    db.add(check)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return TicketCheckResult(check_id=check.id, expires_at=expires, report=report)


@router.post(
    "/tickets/{ticket_id}/diagnostics/confirm",
    status_code=201,
    response_model=DiagnosticView,
)
def confirm_check(
    ticket_id: UUID,
    payload: ConfirmRequest,
    response: Response,
    identity: CsrfIdentityDep,
    db: SessionDep,
    if_match: IfMatch = None,
):
    actor_id = _engineer(identity)
    item = confirm_monitoring_check(db, actor_id, ticket_id, payload.check_id, _version(if_match))
    response.headers["Cache-Control"] = "no-store"
    return item


@router.get(
    "/subscriber-lookups/{lookup_id}/connections/{connection_id}/onu",
    response_model=OnuResult,
)
def subscriber_onu(
    lookup_id: UUID,
    connection_id: UUID,
    response: Response,
    identity: CurrentIdentityDep,
    db: SessionDep,
    runtime: OnuRuntimeDep,
) -> OnuResult:
    actor_id = _engineer(identity)
    snapshot = db.get(SubscriberSnapshot, lookup_id)
    if snapshot is None or snapshot.requested_by != actor_id:
        raise ApiError(404, "lookup_not_found", "Subscriber lookup not found")
    connection = db.get(ConnectionSnapshot, connection_id)
    if connection is None or connection.subscriber_snapshot_id != lookup_id:
        raise ApiError(404, "connection_not_found", "Connection not found")
    db.expunge(connection)
    db.rollback()
    result = lookup_onu_candidates(connection, *runtime)
    _still_engineer(db, actor_id)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/tickets/{ticket_id}/onu", response_model=OnuResult)
def ticket_onu(
    ticket_id: UUID,
    response: Response,
    identity: CurrentIdentityDep,
    db: SessionDep,
    runtime: OnuRuntimeDep,
) -> OnuResult:
    actor_id = _engineer(identity)
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise ApiError(404, "ticket_not_found", "Ticket not found")
    if ticket.connection_snapshot_id is None:
        raise ApiError(409, "connection_required", "Ticket has no confirmed connection")
    connection = db.get(ConnectionSnapshot, ticket.connection_snapshot_id)
    if connection is None:
        raise ApiError(409, "connection_missing", "Confirmed connection is unavailable")
    db.expunge(connection)
    db.rollback()
    result = lookup_onu_candidates(connection, *runtime)
    _still_engineer(db, actor_id)
    response.headers["Cache-Control"] = "no-store"
    return result
