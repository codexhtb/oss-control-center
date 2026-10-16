"""Versioned Service Desk routes backed solely by PostgreSQL."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.identity import CsrfIdentityDep, CurrentIdentityDep, SessionDep
from app.core.errors import ApiError
from app.modules.identity.access import has_permission
from app.modules.identity.models import User
from app.modules.identity.roles import Permission, Role
from app.modules.identity.service import AuthContext, audit
from app.modules.tickets.models import (
    DiagnosticSnapshot,
    SubscriberSnapshot,
    Ticket,
    TicketAudit,
    TicketComment,
    TicketEvent,
    TicketResolution,
)
from app.modules.tickets.schemas import (
    AddComment,
    AddDecision,
    AssignableEngineerView,
    AssignEngineer,
    AuditView,
    CloseTicket,
    CommentView,
    CreateTicket,
    DecisionView,
    DeleteTicket,
    DiagnosticView,
    EditTicket,
    EventView,
    FieldWorkRequest,
    ManualDiagnostic,
    Page,
    PurgeTicket,
    RecentEventView,
    TicketDetail,
    TicketListView,
    TicketView,
)
from app.modules.tickets.service import (
    add_comment,
    add_decision,
    add_diagnostic,
    assign_engineer,
    claim_ticket,
    close_ticket,
    create_ticket,
    delete_ticket,
    edit_ticket,
    list_tickets,
    purge_ticket,
    release_ticket,
    request_field_work,
    restore_ticket,
    start_ticket,
    ticket_detail,
)
from app.modules.tickets.status import TicketPriority, TicketStatus, TicketType

router = APIRouter(prefix="/tickets", tags=["tickets"])
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]
FeedPageNumber = Annotated[int, Query(ge=1, le=1000)]
FeedPageSize = Annotated[int, Query(ge=1, le=50)]
IfMatch = Annotated[str | None, Header(alias="If-Match")]


def _allowed(identity: AuthContext, db: Session, permission: Permission) -> AuthContext:
    if not has_permission(db, identity.user, permission):
        audit(db, "permission_denied", "denied", actor=identity.user.id)
        db.commit()
        raise ApiError(403, "forbidden", "Permission denied")
    return identity


def can_read(identity: CurrentIdentityDep, db: SessionDep) -> AuthContext:
    if not (
        has_permission(db, identity.user, Permission.TICKET_READ)
        or has_permission(db, identity.user, Permission.TICKET_READ_OWN)
    ):
        return _allowed(identity, db, Permission.TICKET_READ)
    return identity


def can_create(identity: CsrfIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db, Permission.TICKET_CREATE)


def can_comment(identity: CsrfIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db, Permission.TICKET_COMMENT)


def can_start(identity: CsrfIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db, Permission.TICKET_START)


def can_assign(identity: CsrfIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db, Permission.TICKET_ASSIGN)


def can_list_engineers(identity: CurrentIdentityDep, db: SessionDep) -> AuthContext:
    if any(
        has_permission(db, identity.user, permission)
        for permission in (
            Permission.TICKET_ASSIGN,
            Permission.TICKET_READ,
            Permission.TICKET_READ_OWN,
        )
    ):
        return identity
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


def can_read_diagnostics(identity: CurrentIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db, Permission.DIAGNOSTICS_READ)


ReadDep = Annotated[AuthContext, Depends(can_read)]
CreateDep = Annotated[AuthContext, Depends(can_create)]
CommentDep = Annotated[AuthContext, Depends(can_comment)]
StartDep = Annotated[AuthContext, Depends(can_start)]
AssignDep = Annotated[AuthContext, Depends(can_assign)]
EngineerListDep = Annotated[AuthContext, Depends(can_list_engineers)]
FieldDep = Annotated[AuthContext, Depends(can_field_work)]
DiagnoseDep = Annotated[AuthContext, Depends(can_diagnose)]
DecisionDep = Annotated[AuthContext, Depends(can_decide)]
CloseDep = Annotated[AuthContext, Depends(can_close)]
AuditDep = Annotated[AuthContext, Depends(can_audit)]
DiagnosticReadDep = Annotated[AuthContext, Depends(can_read_diagnostics)]


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
    field = {
        TicketComment: ("author_id", "author_name"),
        TicketEvent: ("actor_id", "actor_name"),
        TicketResolution: ("engineer_id", "engineer_name"),
    }.get(model)
    if field:
        names = _names(db, [getattr(item, field[0]) for item in items])
        items = [{**item.__dict__, field[1]: names.get(getattr(item, field[0]))} for item in items]
    return {"items": items, "page": page, "size": size, "total": total}


def _names(db: Session, ids: list[UUID | None]) -> dict[UUID, str]:
    valid = {user_id for user_id in ids if user_id is not None}
    if not valid:
        return {}
    return dict(db.execute(select(User.id, User.display_name).where(User.id.in_(valid))).all())


def _ticket_views(db: Session, items: list[Ticket]) -> list[TicketListView]:
    if not items:
        return []
    names = _names(
        db,
        [value for ticket in items for value in (ticket.created_by, ticket.assigned_engineer_id)],
    )
    snapshot_ids = {
        ticket.subscriber_snapshot_id
        for ticket in items
        if ticket.subscriber_snapshot_id is not None
    }
    subscribers = (
        {
            snapshot_id: (
                (full_name or "").strip() or (organization_name or "").strip() or None,
                account_no,
            )
            for snapshot_id, full_name, organization_name, account_no in db.execute(
                select(
                    SubscriberSnapshot.id,
                    SubscriberSnapshot.full_name,
                    SubscriberSnapshot.organization_name,
                    SubscriberSnapshot.account_no,
                ).where(SubscriberSnapshot.id.in_(snapshot_ids))
            )
        }
        if snapshot_ids
        else {}
    )
    # Only the latest assignment/start fact can establish work duration. An old
    # start followed by release or reassignment cannot be attributed to the new assignee.
    latest_assignment = db.execute(
        select(
            TicketEvent.ticket_id,
            TicketEvent.event_type,
            TicketEvent.actor_id,
            TicketEvent.occurred_at,
        )
        .where(
            TicketEvent.ticket_id.in_([item.id for item in items]),
            TicketEvent.event_type.in_(
                ("engineer_claimed", "engineer_released", "engineer_assigned", "ticket_started")
            ),
        )
        .distinct(TicketEvent.ticket_id)
        .order_by(TicketEvent.ticket_id, TicketEvent.ticket_version.desc(), TicketEvent.id.desc())
    ).all()
    latest_by_ticket = {row.ticket_id: row for row in latest_assignment}
    return [
        TicketListView.model_validate(item).model_copy(
            update={
                "created_by_name": names.get(item.created_by),
                "assigned_engineer_name": names.get(item.assigned_engineer_id),
                "subscriber_name": subscribers.get(item.subscriber_snapshot_id, (None, None))[0],
                "account_no": subscribers.get(item.subscriber_snapshot_id, (None, item.account_no))[
                    1
                ],
                "work_started_at": (
                    latest_by_ticket[item.id].occurred_at
                    if item.status == TicketStatus.IN_PROGRESS
                    and item.assigned_engineer_id is not None
                    and item.id in latest_by_ticket
                    and latest_by_ticket[item.id].event_type
                    in ("engineer_claimed", "ticket_started")
                    and latest_by_ticket[item.id].actor_id == item.assigned_engineer_id
                    else None
                ),
            }
        )
        for item in items
    ]


def _readable(db: Session, identity: AuthContext, ticket_id: UUID) -> Ticket:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or ticket.deleted_at is not None:
        raise ApiError(404, "ticket_not_found", "Ticket not found")
    if not has_permission(db, identity.user, Permission.TICKET_READ) and not (
        has_permission(db, identity.user, Permission.TICKET_READ_OWN)
        and ticket.created_by == identity.user.id
    ):
        raise ApiError(404, "ticket_not_found", "Ticket not found")
    return ticket


def _mutation_scope(db: Session, identity: AuthContext, ticket_id: UUID) -> None:
    if not (
        has_permission(db, identity.user, Permission.TICKET_READ)
        or has_permission(db, identity.user, Permission.TICKET_READ_OWN)
    ):
        raise ApiError(403, "forbidden", "Permission denied")
    _readable(db, identity, ticket_id)


@router.post("", status_code=201, response_model=TicketView)
def create(payload: CreateTicket, response: Response, identity: CreateDep, db: SessionDep):
    ticket, created = create_ticket(db, identity.user.id, payload)
    if not created:
        response.status_code = 200
    _etag(response, TicketView.model_validate(ticket))
    return ticket


@router.get("", response_model=Page[TicketListView])
def listing(
    identity: ReadDep,
    db: SessionDep,
    page: PageNumber = 1,
    size: PageSize = 20,
    status: TicketStatus | None = None,
    priority: TicketPriority | None = None,
    number: Annotated[int | None, Query(ge=1)] = None,
    assignee: UUID | None = None,
    unassigned: bool = False,
    account_no: str | None = None,
    ticket_type: TicketType | None = None,
    q: Annotated[str | None, Query(max_length=160)] = None,
    category_id: UUID | None = None,
    category: Annotated[str | None, Query(max_length=160)] = None,
    created_from: datetime | None = None,
    created_before: datetime | None = None,
    active_only: bool = False,
):
    if unassigned and assignee is not None:
        raise ApiError(422, "conflicting_filters", "assignee and unassigned cannot be combined")
    search = q.strip() if q is not None else None
    category_name = category.strip() if category is not None else None
    if any(
        ord(char) < 32 or ord(char) == 127
        for value in (search, category_name)
        if value
        for char in value
    ):
        raise ApiError(422, "invalid_search", "Search contains control characters")
    if created_from is not None and created_from.utcoffset() is None:
        raise ApiError(422, "invalid_date_range", "created_from requires a timezone")
    if created_before is not None and created_before.utcoffset() is None:
        raise ApiError(422, "invalid_date_range", "created_before requires a timezone")
    if created_from is not None and created_before is not None and created_from >= created_before:
        raise ApiError(422, "invalid_date_range", "created_before must follow created_from")
    items, total = list_tickets(
        db,
        page=page,
        size=size,
        status=status,
        priority=priority,
        number=number,
        assignee=assignee,
        unassigned=unassigned,
        account_no=account_no,
        ticket_type=ticket_type,
        search=search,
        category_id=category_id,
        category_name=category_name,
        created_from=created_from,
        created_before=created_before,
        active_only=active_only,
        visible_to=None
        if has_permission(db, identity.user, Permission.TICKET_READ)
        else identity.user.id,
    )
    return {"items": _ticket_views(db, items), "page": page, "size": size, "total": total}


@router.get("/trash", response_model=Page[TicketListView])
def trash(
    identity: CurrentIdentityDep,
    db: SessionDep,
    page: PageNumber = 1,
    size: PageSize = 20,
):
    if not has_permission(db, identity.user, Permission.TICKET_READ) or not (
        has_permission(db, identity.user, Permission.TICKET_RESTORE)
        or has_permission(db, identity.user, Permission.TICKET_PURGE)
    ):
        raise ApiError(403, "forbidden", "Permission denied")
    condition = Ticket.deleted_at.is_not(None)
    total = db.scalar(select(func.count()).select_from(Ticket).where(condition)) or 0
    rows = db.scalars(
        select(Ticket)
        .where(condition)
        .order_by(Ticket.deleted_at.desc(), Ticket.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return {"items": _ticket_views(db, rows), "page": page, "size": size, "total": total}


@router.get("/assignable-engineers", response_model=Page[AssignableEngineerView])
def assignable_engineers(
    identity: EngineerListDep,
    db: SessionDep,
    page: PageNumber = 1,
    size: PageSize = 20,
):
    conditions = (User.is_active.is_(True), User.role == Role.ENGINEER)
    total = db.scalar(select(func.count()).select_from(User).where(*conditions)) or 0
    rows = (
        db.execute(
            select(User.id, User.display_name)
            .where(*conditions)
            .order_by(User.display_name, User.id)
            .offset((page - 1) * size)
            .limit(size)
        )
        .mappings()
        .all()
    )
    return {"items": [dict(row) for row in rows], "page": page, "size": size, "total": total}


@router.get("/events/recent", response_model=Page[RecentEventView])
def recent_events(
    identity: ReadDep,
    db: SessionDep,
    page: FeedPageNumber = 1,
    size: FeedPageSize = 20,
):
    conditions = (
        []
        if has_permission(db, identity.user, Permission.TICKET_READ)
        else [Ticket.created_by == identity.user.id]
    )
    conditions.append(Ticket.deleted_at.is_(None))
    total = (
        db.scalar(
            select(func.count())
            .select_from(TicketEvent)
            .join(Ticket, Ticket.id == TicketEvent.ticket_id)
            .where(*conditions)
        )
        or 0
    )
    rows = (
        db.execute(
            select(
                TicketEvent.id,
                TicketEvent.ticket_id,
                Ticket.number.label("ticket_number"),
                TicketEvent.event_type,
                TicketEvent.actor_id,
                TicketEvent.ticket_version,
                TicketEvent.occurred_at,
            )
            .join(Ticket, Ticket.id == TicketEvent.ticket_id)
            .where(*conditions)
            .order_by(TicketEvent.occurred_at.desc(), TicketEvent.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
        .mappings()
        .all()
    )
    names = _names(db, [row["actor_id"] for row in rows])
    return {
        "items": [{**row, "actor_name": names.get(row["actor_id"])} for row in rows],
        "page": page,
        "size": size,
        "total": total,
    }


@router.get("/{ticket_id}", response_model=TicketDetail)
def get_one(ticket_id: UUID, response: Response, identity: ReadDep, db: SessionDep):
    _readable(db, identity, ticket_id)
    ticket = ticket_detail(db, ticket_id)
    names = _names(db, [ticket.created_by, ticket.assigned_engineer_id])
    ticket = ticket.model_copy(
        update={
            "created_by_name": names.get(ticket.created_by),
            "assigned_engineer_name": names.get(ticket.assigned_engineer_id),
        }
    )
    _etag(response, ticket)
    return ticket


@router.patch("/{ticket_id}", response_model=TicketView)
def edit(
    ticket_id: UUID,
    payload: EditTicket,
    response: Response,
    identity: CsrfIdentityDep,
    db: SessionDep,
    if_match: IfMatch = None,
):
    ticket = edit_ticket(db, identity.user.id, ticket_id, payload, _version(if_match))
    _etag(response, TicketView.model_validate(ticket))
    return ticket


@router.delete("/{ticket_id}", response_model=TicketView)
def soft_delete(
    ticket_id: UUID,
    response: Response,
    identity: CsrfIdentityDep,
    db: SessionDep,
    payload: DeleteTicket | None = None,
    if_match: IfMatch = None,
):
    ticket = delete_ticket(
        db, identity.user.id, ticket_id, payload or DeleteTicket(), _version(if_match)
    )
    _etag(response, TicketView.model_validate(ticket))
    return ticket


@router.post("/{ticket_id}/restore", response_model=TicketView)
def restore(
    ticket_id: UUID,
    response: Response,
    identity: CsrfIdentityDep,
    db: SessionDep,
    if_match: IfMatch = None,
):
    ticket = restore_ticket(db, identity.user.id, ticket_id, _version(if_match))
    _etag(response, TicketView.model_validate(ticket))
    return ticket


@router.delete("/{ticket_id}/purge", status_code=204)
def purge(
    ticket_id: UUID,
    payload: PurgeTicket,
    identity: CsrfIdentityDep,
    db: SessionDep,
    if_match: IfMatch = None,
) -> Response:
    purge_ticket(db, identity.user.id, ticket_id, _version(if_match), payload.confirm_number)
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/{ticket_id}/assignment", response_model=TicketView)
def assign(
    ticket_id: UUID,
    payload: AssignEngineer,
    response: Response,
    identity: AssignDep,
    db: SessionDep,
    if_match: IfMatch = None,
):
    _mutation_scope(db, identity, ticket_id)
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
    _mutation_scope(db, identity, ticket_id)
    ticket = start_ticket(db, identity.user.id, ticket_id, _version(if_match))
    _etag(response, TicketView.model_validate(ticket))
    return ticket


@router.post("/{ticket_id}/claim", response_model=TicketView)
def claim(
    ticket_id: UUID,
    response: Response,
    identity: StartDep,
    db: SessionDep,
    if_match: IfMatch = None,
):
    _mutation_scope(db, identity, ticket_id)
    ticket = claim_ticket(db, identity.user.id, ticket_id, _version(if_match))
    _etag(response, TicketView.model_validate(ticket))
    return ticket


@router.post("/{ticket_id}/release", response_model=TicketView)
def release(
    ticket_id: UUID,
    response: Response,
    identity: StartDep,
    db: SessionDep,
    if_match: IfMatch = None,
):
    _mutation_scope(db, identity, ticket_id)
    ticket = release_ticket(db, identity.user.id, ticket_id, _version(if_match))
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
    _mutation_scope(db, identity, ticket_id)
    ticket = request_field_work(db, identity.user.id, ticket_id, payload, _version(if_match))
    _etag(response, TicketView.model_validate(ticket))
    return ticket


@router.post("/{ticket_id}/comments", status_code=201, response_model=CommentView)
def comment(ticket_id: UUID, payload: AddComment, identity: CommentDep, db: SessionDep):
    _mutation_scope(db, identity, ticket_id)
    return add_comment(db, identity.user.id, ticket_id, payload)


@router.get("/{ticket_id}/comments", response_model=Page[CommentView])
def comments(
    ticket_id: UUID,
    identity: ReadDep,
    db: SessionDep,
    page: PageNumber = 1,
    size: PageSize = 20,
):
    _readable(db, identity, ticket_id)
    return _page(db, TicketComment, ticket_id, page, size, TicketComment.created_at)


@router.post("/{ticket_id}/manual-diagnostics", status_code=201, response_model=DiagnosticView)
def diagnostic(
    ticket_id: UUID,
    payload: ManualDiagnostic,
    identity: DiagnoseDep,
    db: SessionDep,
):
    _mutation_scope(db, identity, ticket_id)
    return add_diagnostic(db, identity.user.id, ticket_id, payload)


@router.get("/{ticket_id}/manual-diagnostics", response_model=Page[DiagnosticView])
def diagnostics(
    ticket_id: UUID,
    identity: DiagnosticReadDep,
    db: SessionDep,
    page: PageNumber = 1,
    size: PageSize = 20,
):
    _readable(db, identity, ticket_id)
    return _page(db, DiagnosticSnapshot, ticket_id, page, size, DiagnosticSnapshot.recorded_at)


@router.post("/{ticket_id}/decisions", status_code=201, response_model=DecisionView)
def decision(ticket_id: UUID, payload: AddDecision, identity: DecisionDep, db: SessionDep):
    _mutation_scope(db, identity, ticket_id)
    return add_decision(db, identity.user.id, ticket_id, payload)


@router.get("/{ticket_id}/decisions", response_model=Page[DecisionView])
def decisions(
    ticket_id: UUID,
    identity: ReadDep,
    db: SessionDep,
    page: PageNumber = 1,
    size: PageSize = 20,
):
    _readable(db, identity, ticket_id)
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
    _mutation_scope(db, identity, ticket_id)
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
    _readable(db, identity, ticket_id)
    return _page(db, TicketEvent, ticket_id, page, size, TicketEvent.occurred_at)


@router.get("/{ticket_id}/audit", response_model=Page[AuditView])
def audit_events(
    ticket_id: UUID,
    identity: AuditDep,
    db: SessionDep,
    page: PageNumber = 1,
    size: PageSize = 20,
):
    _readable(db, identity, ticket_id)
    return _page(db, TicketAudit, ticket_id, page, size, TicketAudit.occurred_at)
