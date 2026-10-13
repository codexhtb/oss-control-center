"""Versioned Service Desk routes backed solely by PostgreSQL."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.identity import CsrfIdentityDep, CurrentIdentityDep, SessionDep
from app.core.errors import ApiError
from app.modules.identity.roles import Permission, Role, permissions_for
from app.modules.identity.service import AuthContext, audit
from app.modules.tickets.models import (
    DiagnosticSnapshot,
    TicketAudit,
    TicketComment,
    TicketEvent,
    TicketResolution,
)
from app.modules.tickets.schemas import (
    AddComment,
    AddDecision,
    AssignEngineer,
    AuditView,
    CloseTicket,
    CommentView,
    CreateTicket,
    DecisionView,
    DiagnosticView,
    EventView,
    FieldWorkRequest,
    ManualDiagnostic,
    Page,
    TicketDetail,
    TicketView,
)
from app.modules.tickets.service import (
    add_comment,
    add_decision,
    add_diagnostic,
    assign_engineer,
    close_ticket,
    create_ticket,
    list_tickets,
    request_field_work,
    start_ticket,
    ticket_detail,
)
from app.modules.tickets.status import TicketPriority, TicketStatus

router = APIRouter(prefix="/tickets", tags=["tickets"])
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
IfMatch = Annotated[str | None, Header(alias="If-Match")]


def _allowed(identity: AuthContext, db: Session, permission: Permission) -> AuthContext:
    if permission not in permissions_for(Role(identity.user.role)):
        audit(db, "permission_denied", "denied", actor=identity.user.id)
        db.commit()
        raise ApiError(403, "forbidden", "Permission denied")
    return identity


def can_read(identity: CurrentIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db, Permission.TICKET_READ)


def can_create(identity: CsrfIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db, Permission.TICKET_CREATE)


def can_comment(identity: CsrfIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db, Permission.TICKET_COMMENT)


def can_start(identity: CsrfIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db, Permission.TICKET_START)


def can_assign(identity: CsrfIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db, Permission.TICKET_ASSIGN)


def can_field_work(identity: CsrfIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db, Permission.TICKET_FIELD_WORK)


def can_diagnose(identity: CsrfIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db, Permission.TICKET_MANUAL_DIAGNOSTIC)


def can_decide(identity: CsrfIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db, Permission.TICKET_DECISION)


def can_close(identity: CsrfIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db, Permission.TICKET_CLOSE)


def can_audit(identity: CurrentIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db, Permission.TICKET_AUDIT_READ)


ReadDep = Annotated[AuthContext, Depends(can_read)]
CreateDep = Annotated[AuthContext, Depends(can_create)]
CommentDep = Annotated[AuthContext, Depends(can_comment)]
StartDep = Annotated[AuthContext, Depends(can_start)]
AssignDep = Annotated[AuthContext, Depends(can_assign)]
FieldDep = Annotated[AuthContext, Depends(can_field_work)]
DiagnoseDep = Annotated[AuthContext, Depends(can_diagnose)]
DecisionDep = Annotated[AuthContext, Depends(can_decide)]
CloseDep = Annotated[AuthContext, Depends(can_close)]
AuditDep = Annotated[AuthContext, Depends(can_audit)]


def _version(raw: IfMatch) -> int | None:
    if raw is None:
        return None
    value = raw.strip('"')
    if not value.isdecimal() or int(value) < 1:
        raise ApiError(400, "invalid_if_match", "If-Match must be a ticket version")
    return int(value)


def _etag(response: Response, ticket: TicketView) -> None:
    response.headers["ETag"] = f'"{ticket.version}"'
    response.headers["Cache-Control"] = "no-store"


def _page(db: Session, model, ticket_id: UUID, page: int, size: int, order_field):
    total = (
        db.scalar(select(func.count()).select_from(model).where(model.ticket_id == ticket_id)) or 0
    )
    items = db.scalars(
        select(model)
        .where(model.ticket_id == ticket_id)
        .order_by(order_field, model.id)
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return {"items": items, "page": page, "size": size, "total": total}


@router.post("", status_code=201, response_model=TicketView)
def create(payload: CreateTicket, response: Response, identity: CreateDep, db: SessionDep):
    ticket, created = create_ticket(db, identity.user.id, payload)
    if not created:
        response.status_code = 200
    _etag(response, TicketView.model_validate(ticket))
    return ticket


@router.get("", response_model=Page[TicketView])
def listing(
    identity: ReadDep,
    db: SessionDep,
    page: PageNumber = 1,
    size: PageSize = 20,
    status: TicketStatus | None = None,
    priority: TicketPriority | None = None,
    number: Annotated[int | None, Query(ge=1)] = None,
    assignee: UUID | None = None,
    account_no: str | None = None,
):
    items, total = list_tickets(
        db,
        page=page,
        size=size,
        status=status,
        priority=priority,
        number=number,
        assignee=assignee,
        account_no=account_no,
    )
    return {"items": items, "page": page, "size": size, "total": total}


@router.get("/{ticket_id}", response_model=TicketDetail)
def get_one(ticket_id: UUID, response: Response, identity: ReadDep, db: SessionDep):
    ticket = ticket_detail(db, ticket_id)
    _etag(response, ticket)
    return ticket


@router.post("/{ticket_id}/assignment", response_model=TicketView)
def assign(
    ticket_id: UUID,
    payload: AssignEngineer,
    response: Response,
    identity: AssignDep,
    db: SessionDep,
    if_match: IfMatch = None,
):
    ticket = assign_engineer(
        db, identity.user.id, ticket_id, payload.engineer_id, _version(if_match)
    )
    _etag(response, TicketView.model_validate(ticket))
    return ticket


@router.post("/{ticket_id}/start", response_model=TicketView)
def start(
    ticket_id: UUID,
    response: Response,
    identity: StartDep,
    db: SessionDep,
    if_match: IfMatch = None,
):
    ticket = start_ticket(db, identity.user.id, ticket_id, _version(if_match))
    _etag(response, TicketView.model_validate(ticket))
    return ticket


@router.post("/{ticket_id}/field-work-request", response_model=TicketView)
def field_work(
    ticket_id: UUID,
    payload: FieldWorkRequest,
    response: Response,
    identity: FieldDep,
    db: SessionDep,
    if_match: IfMatch = None,
):
    ticket = request_field_work(db, identity.user.id, ticket_id, payload, _version(if_match))
    _etag(response, TicketView.model_validate(ticket))
    return ticket


@router.post("/{ticket_id}/comments", status_code=201, response_model=CommentView)
def comment(ticket_id: UUID, payload: AddComment, identity: CommentDep, db: SessionDep):
    return add_comment(db, identity.user.id, ticket_id, payload)


@router.get("/{ticket_id}/comments", response_model=Page[CommentView])
def comments(
    ticket_id: UUID,
    identity: ReadDep,
    db: SessionDep,
    page: PageNumber = 1,
    size: PageSize = 20,
):
    ticket_detail(db, ticket_id)
    return _page(db, TicketComment, ticket_id, page, size, TicketComment.created_at)


@router.post("/{ticket_id}/manual-diagnostics", status_code=201, response_model=DiagnosticView)
def diagnostic(
    ticket_id: UUID,
    payload: ManualDiagnostic,
    identity: DiagnoseDep,
    db: SessionDep,
):
    return add_diagnostic(db, identity.user.id, ticket_id, payload)


@router.get("/{ticket_id}/manual-diagnostics", response_model=Page[DiagnosticView])
def diagnostics(
    ticket_id: UUID,
    identity: ReadDep,
    db: SessionDep,
    page: PageNumber = 1,
    size: PageSize = 20,
):
    ticket_detail(db, ticket_id)
    return _page(db, DiagnosticSnapshot, ticket_id, page, size, DiagnosticSnapshot.recorded_at)


@router.post("/{ticket_id}/decisions", status_code=201, response_model=DecisionView)
def decision(ticket_id: UUID, payload: AddDecision, identity: DecisionDep, db: SessionDep):
    return add_decision(db, identity.user.id, ticket_id, payload)


@router.get("/{ticket_id}/decisions", response_model=Page[DecisionView])
def decisions(
    ticket_id: UUID,
    identity: ReadDep,
    db: SessionDep,
    page: PageNumber = 1,
    size: PageSize = 20,
):
    ticket_detail(db, ticket_id)
    return _page(db, TicketResolution, ticket_id, page, size, TicketResolution.created_at)


@router.post("/{ticket_id}/close", response_model=TicketView)
def close(
    ticket_id: UUID,
    payload: CloseTicket,
    response: Response,
    identity: CloseDep,
    db: SessionDep,
    if_match: IfMatch = None,
):
    ticket = close_ticket(db, identity.user.id, ticket_id, payload.decision_id, _version(if_match))
    _etag(response, TicketView.model_validate(ticket))
    return ticket


@router.get("/{ticket_id}/history", response_model=Page[EventView])
def history(
    ticket_id: UUID,
    identity: ReadDep,
    db: SessionDep,
    page: PageNumber = 1,
    size: PageSize = 20,
):
    ticket_detail(db, ticket_id)
    return _page(db, TicketEvent, ticket_id, page, size, TicketEvent.occurred_at)


@router.get("/{ticket_id}/audit", response_model=Page[AuditView])
def audit_events(
    ticket_id: UUID,
    identity: AuditDep,
    db: SessionDep,
    page: PageNumber = 1,
    size: PageSize = 20,
):
    ticket_detail(db, ticket_id)
    return _page(db, TicketAudit, ticket_id, page, size, TicketAudit.occurred_at)
