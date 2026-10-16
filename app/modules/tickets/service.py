"""Transactional Ticket Engine with row locks, version checks and append-only facts."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.identity.access import has_permission
from app.modules.identity.models import User
from app.modules.identity.roles import Permission, Role
from app.modules.tickets.duplicates import active_count, lock_subscriber
from app.modules.tickets.models import (
    ConnectionSnapshot,
    DiagnosticCheck,
    DiagnosticSnapshot,
    SubscriberSnapshot,
    Ticket,
    TicketAudit,
    TicketComment,
    TicketEvent,
    TicketPurgeAudit,
    TicketResolution,
)
from app.modules.tickets.schemas import (
    AddComment,
    AddDecision,
    CreateTicket,
    DeleteTicket,
    EditTicket,
    FieldWorkRequest,
    ManualDiagnostic,
    TicketDetail,
)
from app.modules.tickets.status import TicketStatus, TicketType


def _actor(db: Session, actor_id: UUID, permission: Permission) -> User:
    user = db.execute(
        select(User)
        .where(User.id == actor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if not user or not user.is_active or not has_permission(db, user, permission):
        raise ApiError(403, "forbidden", "Permission denied")
    return user


def _ticket(
    db: Session, ticket_id: UUID, *, lock: bool = False, include_deleted: bool = False
) -> Ticket:
    statement = select(Ticket).where(Ticket.id == ticket_id)
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    ticket = db.execute(statement).scalar_one_or_none()
    if ticket is None or (ticket.deleted_at is not None and not include_deleted):
        raise ApiError(404, "ticket_not_found", "Ticket not found")
    return ticket


def _version(ticket: Ticket, expected: int | None) -> None:
    if expected is None:
        raise ApiError(428, "version_required", "If-Match ticket version is required")
    if expected != ticket.version:
        raise ApiError(409, "version_conflict", "Ticket was changed; reload it")


def _open(ticket: Ticket) -> None:
    if ticket.status == TicketStatus.CLOSED:
        raise ApiError(409, "ticket_closed", "Closed ticket cannot be changed")


def _fact(db: Session, ticket: Ticket, actor_id: UUID, event_type: str, data: dict) -> None:
    db.add(
        TicketEvent(
            ticket_id=ticket.id,
            event_type=event_type,
            actor_id=actor_id,
            event_data=data,
            ticket_version=ticket.version,
        )
    )
    db.add(
        TicketAudit(
            ticket_id=ticket.id,
            actor_id=actor_id,
            action=event_type,
            outcome="success",
            origin="API",
        )
    )


def _changed(ticket: Ticket) -> None:
    ticket.version += 1
    ticket.updated_at = datetime.now(UTC)


def edit_ticket(
    db: Session, actor_id: UUID, ticket_id: UUID, payload: EditTicket, version: int | None
) -> Ticket:
    actor = db.execute(
        select(User)
        .where(User.id == actor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if actor is None or not actor.is_active:
        raise ApiError(403, "forbidden", "Permission denied")
    ticket = _ticket(db, ticket_id, lock=True)
    can_edit_all = has_permission(db, actor, Permission.TICKET_EDIT_ALL)
    can_edit_own = has_permission(db, actor, Permission.TICKET_EDIT_OWN)
    if not can_edit_all and not (can_edit_own and ticket.created_by == actor_id):
        raise ApiError(403, "forbidden", "Permission denied")
    if not has_permission(db, actor, Permission.TICKET_READ) and not (
        has_permission(db, actor, Permission.TICKET_READ_OWN) and ticket.created_by == actor_id
    ):
        raise ApiError(403, "forbidden", "Permission denied")
    _version(ticket, version)
    _open(ticket)
    changed: dict[str, dict[str, str | None]] = {}
    for field in ("subject", "description"):
        if field in payload.model_fields_set:
            value = getattr(payload, field)
            previous = getattr(ticket, field)
            if value != previous:
                changed[field] = {"before": previous, "after": value}
                setattr(ticket, field, value)
    if not changed:
        raise ApiError(409, "no_change", "No fields changed")
    _changed(ticket)
    _fact(db, ticket, actor_id, "ticket_edited", {"changes": changed})
    db.commit()
    db.refresh(ticket)
    return ticket


def _can_read_ticket(db: Session, actor: User, ticket: Ticket) -> bool:
    return has_permission(db, actor, Permission.TICKET_READ) or (
        has_permission(db, actor, Permission.TICKET_READ_OWN) and ticket.created_by == actor.id
    )


def delete_ticket(
    db: Session, actor_id: UUID, ticket_id: UUID, payload: DeleteTicket, version: int | None
) -> Ticket:
    actor = db.execute(
        select(User)
        .where(User.id == actor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if actor is None or not actor.is_active:
        raise ApiError(403, "forbidden", "Permission denied")
    ticket = _ticket(db, ticket_id, lock=True, include_deleted=True)
    if ticket.deleted_at is not None:
        raise ApiError(409, "already_deleted", "Ticket is already deleted")
    if not _can_read_ticket(db, actor, ticket):
        raise ApiError(404, "ticket_not_found", "Ticket not found")
    if not has_permission(db, actor, Permission.TICKET_DELETE_ALL) and not (
        has_permission(db, actor, Permission.TICKET_DELETE_OWN) and ticket.created_by == actor_id
    ):
        raise ApiError(403, "forbidden", "Permission denied")
    _version(ticket, version)
    ticket.deleted_at = datetime.now(UTC)
    ticket.deleted_by = actor_id
    ticket.delete_reason = payload.reason
    _changed(ticket)
    _fact(
        db,
        ticket,
        actor_id,
        "ticket_deleted",
        {"reason": payload.reason} if payload.reason is not None else {},
    )
    db.commit()
    db.refresh(ticket)
    return ticket


def restore_ticket(db: Session, actor_id: UUID, ticket_id: UUID, version: int | None) -> Ticket:
    actor = _actor(db, actor_id, Permission.TICKET_RESTORE)
    if not has_permission(db, actor, Permission.TICKET_READ):
        raise ApiError(403, "forbidden", "Permission denied")
    ticket = _ticket(db, ticket_id, lock=True, include_deleted=True)
    if ticket.deleted_at is None:
        raise ApiError(409, "not_deleted", "Ticket is not deleted")
    _version(ticket, version)
    ticket.deleted_at = None
    ticket.deleted_by = None
    ticket.delete_reason = None
    _changed(ticket)
    _fact(db, ticket, actor_id, "ticket_restored", {})
    db.commit()
    db.refresh(ticket)
    return ticket


def purge_ticket(
    db: Session, actor_id: UUID, ticket_id: UUID, version: int | None, confirm_number: int
) -> None:
    actor = _actor(db, actor_id, Permission.TICKET_PURGE)
    if not has_permission(db, actor, Permission.TICKET_READ):
        raise ApiError(403, "forbidden", "Permission denied")
    ticket = _ticket(db, ticket_id, lock=True, include_deleted=True)
    if ticket.deleted_at is None:
        raise ApiError(409, "not_deleted", "Delete the ticket before purging it")
    _version(ticket, version)
    if ticket.number != confirm_number:
        raise ApiError(422, "confirmation_mismatch", "Ticket number does not match")
    db.execute(
        text("SELECT set_config('oss.purge_ticket_id', :ticket_id, true)"),
        {"ticket_id": str(ticket_id)},
    )
    db.execute(
        text("SELECT set_config('oss.purge_actor_id', :actor_id, true)"),
        {"actor_id": str(actor_id)},
    )
    if ticket.closed_decision_id is not None:
        # Break the closed ticket / decision FK cycle inside this one transaction.
        ticket.status = TicketStatus.IN_PROGRESS
        ticket.closed_decision_id = None
        ticket.closed_by = None
        ticket.closed_at = None
        db.flush()
    for model in (
        DiagnosticSnapshot,
        DiagnosticCheck,
        TicketComment,
        TicketEvent,
        TicketAudit,
        TicketResolution,
    ):
        db.execute(delete(model).where(model.ticket_id == ticket_id))
    db.execute(delete(Ticket).where(Ticket.id == ticket_id))
    db.add(TicketPurgeAudit(ticket_id=ticket_id, ticket_number=ticket.number, actor_id=actor_id))
    db.commit()


def create_ticket(db: Session, actor_id: UUID, payload: CreateTicket) -> tuple[Ticket, bool]:
    _actor(db, actor_id, Permission.TICKET_CREATE)
    existing = db.scalar(
        select(Ticket).where(
            Ticket.created_by == actor_id, Ticket.client_request_id == payload.client_request_id
        )
    )
    if existing:
        return _replayed(db, existing, payload)
    account_no = None
    snapshot_id = None
    selected_service = None
    selected = None
    duplicate_reason = payload.duplicate_reason
    if payload.ticket_type == TicketType.SUBSCRIBER_LINKED:
        account_no, snapshot_id, selected_service, selected = _linked_source(db, actor_id, payload)
        snapshot = db.get(SubscriberSnapshot, snapshot_id)
        lock_subscriber(db, snapshot)
        if active_count(db, snapshot):
            if payload.duplicate_reason is None:
                raise ApiError(
                    409,
                    "active_ticket_exists",
                    "An active ticket exists for this subscriber; check it before creating another",
                )
    ticket = Ticket(
        ticket_type=payload.ticket_type,
        account_no=account_no,
        subscriber_snapshot_id=snapshot_id,
        connection_snapshot_id=selected,
        selected_service_id=selected_service,
        description=payload.description,
        subject=payload.subject,
        status=TicketStatus.OPEN,
        priority=payload.priority,
        created_by=actor_id,
        client_request_id=payload.client_request_id,
        version=1,
    )
    db.add(ticket)
    try:
        db.flush()
        event_data = {"status": TicketStatus.OPEN}
        if duplicate_reason is not None:
            event_data["duplicate_reason"] = duplicate_reason
        _fact(db, ticket, actor_id, "ticket_created", event_data)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(
            select(Ticket).where(
                Ticket.created_by == actor_id, Ticket.client_request_id == payload.client_request_id
            )
        )
        if existing:
            return _replayed(db, existing, payload)
        raise ApiError(409, "ticket_conflict", "Ticket could not be created") from None
    db.refresh(ticket)
    return ticket, True


def _linked_source(
    db: Session, actor_id: UUID, payload: CreateTicket
) -> tuple[str, UUID, str | None, UUID | None]:
    # The schema requires a lookup for linked tickets; keep the check at the service boundary.
    if payload.lookup_id is None:
        raise ApiError(422, "lookup_required", "Subscriber lookup is required")
    snapshot = db.get(SubscriberSnapshot, payload.lookup_id)
    if snapshot is None or snapshot.requested_by != actor_id:
        raise ApiError(404, "lookup_not_found", "Subscriber lookup not found")
    if snapshot.expires_at <= datetime.now(UTC):
        raise ApiError(409, "lookup_expired", "Subscriber lookup expired")
    service_ids = [item.get("service_id") or item.get("id") for item in snapshot.services]
    if any(not isinstance(item, str) or not item for item in service_ids):
        raise ApiError(409, "invalid_snapshot", "Snapshot services are invalid")
    if len(set(service_ids)) != len(service_ids):
        raise ApiError(409, "invalid_snapshot", "Snapshot services are ambiguous")
    selected_service = payload.selected_service_id
    if len(service_ids) > 1 and selected_service is None:
        raise ApiError(422, "service_required", "Select a service")
    if selected_service is not None and selected_service not in service_ids:
        raise ApiError(422, "invalid_service", "Service does not belong to lookup")
    if len(service_ids) == 1 and selected_service is None:
        selected_service = service_ids[0]
    selected_service_account = next(
        (
            item.get("account_id")
            for item in snapshot.services
            if (item.get("service_id") or item.get("id")) == selected_service
        ),
        None,
    )
    if selected_service_account is not None and selected_service_account != snapshot.account_no:
        raise ApiError(422, "service_account_mismatch", "Search the service account first")
    connections = db.scalars(
        select(ConnectionSnapshot).where(ConnectionSnapshot.subscriber_snapshot_id == snapshot.id)
    ).all()
    selected = payload.connection_snapshot_id
    if len(connections) > 1 and selected is None:
        raise ApiError(422, "connection_required", "Select a connection")
    if selected is not None and selected not in {item.id for item in connections}:
        raise ApiError(422, "invalid_connection", "Connection does not belong to lookup")
    if len(connections) == 1 and selected is None:
        selected = connections[0].id
    selected_connection = next((item for item in connections if item.id == selected), None)
    if (
        selected_connection is not None
        and selected_connection.service_id is not None
        and selected_connection.service_id != selected_service
    ):
        raise ApiError(422, "service_connection_mismatch", "Connection belongs to another service")
    return snapshot.account_no, snapshot.id, selected_service, selected


def _replayed(db: Session, ticket: Ticket, payload: CreateTicket) -> tuple[Ticket, bool]:
    if ticket.deleted_at is not None:
        raise ApiError(409, "ticket_deleted", "Request already created a deleted ticket")
    if (
        ticket.ticket_type != payload.ticket_type
        or ticket.description != payload.description
        or ticket.subject != payload.subject
        or ticket.priority != payload.priority
    ):
        raise ApiError(409, "request_id_conflict", "Request ID was used for different content")
    created_event = db.scalar(
        select(TicketEvent).where(
            TicketEvent.ticket_id == ticket.id, TicketEvent.event_type == "ticket_created"
        )
    )
    recorded_reason = (
        created_event.event_data.get("duplicate_reason") if created_event is not None else None
    )
    if recorded_reason != payload.duplicate_reason:
        raise ApiError(409, "request_id_conflict", "Request ID was used for different content")
    if payload.ticket_type != TicketType.SUBSCRIBER_LINKED:
        return ticket, False
    selected = payload.connection_snapshot_id
    if selected is None and ticket.connection_snapshot_id is not None:
        connections = db.scalars(
            select(ConnectionSnapshot.id).where(
                ConnectionSnapshot.subscriber_snapshot_id == ticket.subscriber_snapshot_id
            )
        ).all()
        if connections == [ticket.connection_snapshot_id]:
            selected = ticket.connection_snapshot_id
    selected_service = payload.selected_service_id
    if selected_service is None and ticket.selected_service_id is not None:
        snapshot = db.get(SubscriberSnapshot, ticket.subscriber_snapshot_id)
        service_ids = [item.get("service_id") or item.get("id") for item in snapshot.services]
        if service_ids == [ticket.selected_service_id]:
            selected_service = ticket.selected_service_id
    if (
        ticket.subscriber_snapshot_id != payload.lookup_id
        or ticket.connection_snapshot_id != selected
        or ticket.selected_service_id != selected_service
    ):
        raise ApiError(409, "request_id_conflict", "Request ID was used for different content")
    return ticket, False


def ticket_detail(db: Session, ticket_id: UUID) -> TicketDetail:
    ticket = _ticket(db, ticket_id)
    snapshot = (
        db.get(SubscriberSnapshot, ticket.subscriber_snapshot_id)
        if ticket.subscriber_snapshot_id is not None
        else None
    )
    connections = (
        db.scalars(
            select(ConnectionSnapshot).where(
                ConnectionSnapshot.subscriber_snapshot_id == snapshot.id
            )
        ).all()
        if snapshot is not None
        else []
    )
    subscriber = (
        {
            "id": snapshot.id,
            "account_no": snapshot.account_no,
            "subscriber_id": snapshot.subscriber_id,
            "organization_name": snapshot.organization_name,
            "email": snapshot.email,
            "actual_address": snapshot.actual_address,
            "accounts": snapshot.accounts,
            "field_meta": snapshot.field_meta,
            "source": snapshot.source,
            "fetched_at": snapshot.fetched_at,
            "full_name": snapshot.full_name,
            "phone": snapshot.phone,
            "contacts": snapshot.contacts,
            "contracts": snapshot.contracts,
            "services": snapshot.services,
            "connections": connections,
        }
        if snapshot is not None
        else None
    )
    selected = next(
        (item for item in connections if item.id == ticket.connection_snapshot_id), None
    )
    return TicketDetail.model_validate(
        {
            **{
                field: getattr(ticket, field)
                for field in TicketDetail.model_fields
                if hasattr(ticket, field)
            },
            "subscriber": subscriber,
            "selected_connection": selected,
            "technical_state": "UNKNOWN",
        }
    )


def list_tickets(
    db: Session,
    *,
    page: int,
    size: int,
    status: TicketStatus | None,
    priority: str | None,
    number: int | None,
    assignee: UUID | None,
    account_no: str | None,
    ticket_type: TicketType | None = None,
    unassigned: bool = False,
    visible_to: UUID | None = None,
) -> tuple[list[Ticket], int]:
    conditions = [Ticket.deleted_at.is_(None)]
    if visible_to is not None:
        conditions.append(Ticket.created_by == visible_to)
    if status is not None:
        conditions.append(Ticket.status == status)
    if priority is not None:
        conditions.append(Ticket.priority == priority)
    if number is not None:
        conditions.append(Ticket.number == number)
    if assignee is not None:
        conditions.append(Ticket.assigned_engineer_id == assignee)
    if unassigned:
        conditions.append(Ticket.assigned_engineer_id.is_(None))
    if account_no is not None:
        conditions.append(Ticket.account_no == account_no)
    if ticket_type is not None:
        conditions.append(Ticket.ticket_type == ticket_type)
    total = db.scalar(select(func.count()).select_from(Ticket).where(*conditions)) or 0
    items = db.scalars(
        select(Ticket)
        .where(*conditions)
        .order_by(Ticket.created_at.desc(), Ticket.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return items, total


def assign_engineer(
    db: Session, actor_id: UUID, ticket_id: UUID, engineer_id: UUID, version: int | None
) -> Ticket:
    _actor(db, actor_id, Permission.TICKET_ASSIGN)
    ticket = _ticket(db, ticket_id, lock=True)
    _version(ticket, version)
    _open(ticket)
    engineer = db.execute(
        select(User).where(User.id == engineer_id).with_for_update()
    ).scalar_one_or_none()
    if engineer is None or not engineer.is_active or engineer.role != Role.ENGINEER:
        raise ApiError(422, "invalid_engineer", "Assignee must be an active engineer")
    if ticket.assigned_engineer_id == engineer_id:
        raise ApiError(409, "same_assignee", "Engineer is already assigned")
    ticket.assigned_engineer_id = engineer_id
    _changed(ticket)
    _fact(db, ticket, actor_id, "engineer_assigned", {"engineer_id": str(engineer_id)})
    db.commit()
    db.refresh(ticket)
    return ticket


def add_comment(db: Session, actor_id: UUID, ticket_id: UUID, payload: AddComment) -> TicketComment:
    _actor(db, actor_id, Permission.TICKET_COMMENT)
    ticket = _ticket(db, ticket_id, lock=True)
    _open(ticket)
    item = TicketComment(ticket_id=ticket_id, author_id=actor_id, body=payload.body)
    db.add(item)
    db.flush()
    _changed(ticket)
    _fact(db, ticket, actor_id, "comment_added", {"comment_id": str(item.id)})
    db.commit()
    db.refresh(item)
    return item


def start_ticket(db: Session, actor_id: UUID, ticket_id: UUID, version: int | None) -> Ticket:
    _actor(db, actor_id, Permission.TICKET_START)
    ticket = _ticket(db, ticket_id, lock=True)
    _version(ticket, version)
    if ticket.status != TicketStatus.OPEN:
        raise ApiError(409, "invalid_transition", "Ticket cannot be started")
    ticket.status = TicketStatus.IN_PROGRESS
    _changed(ticket)
    _fact(db, ticket, actor_id, "ticket_started", {"from": "OPEN", "to": "IN_PROGRESS"})
    db.commit()
    db.refresh(ticket)
    return ticket


def request_field_work(
    db: Session, actor_id: UUID, ticket_id: UUID, payload: FieldWorkRequest, version: int | None
) -> Ticket:
    _actor(db, actor_id, Permission.TICKET_FIELD_WORK)
    ticket = _ticket(db, ticket_id, lock=True)
    _version(ticket, version)
    if ticket.status != TicketStatus.IN_PROGRESS:
        raise ApiError(409, "invalid_transition", "Start ticket before requesting field work")
    _changed(ticket)
    _fact(db, ticket, actor_id, "field_work_requested", {"reason": payload.reason})
    db.commit()
    db.refresh(ticket)
    return ticket


def add_diagnostic(
    db: Session, actor_id: UUID, ticket_id: UUID, payload: ManualDiagnostic
) -> DiagnosticSnapshot:
    _actor(db, actor_id, Permission.TICKET_MANUAL_DIAGNOSTIC)
    ticket = _ticket(db, ticket_id, lock=True)
    if ticket.status != TicketStatus.IN_PROGRESS:
        raise ApiError(409, "invalid_transition", "Start ticket before recording diagnostics")
    if (
        payload.connection_snapshot_id is not None
        and payload.connection_snapshot_id != ticket.connection_snapshot_id
    ):
        raise ApiError(422, "invalid_connection", "Connection is not selected for ticket")
    item = DiagnosticSnapshot(
        ticket_id=ticket.id,
        subscriber_snapshot_id=ticket.subscriber_snapshot_id,
        connection_snapshot_id=payload.connection_snapshot_id,
        source_kind="MANUAL",
        availability="KNOWN",
        observation={"finding": payload.finding},
        observed_at=payload.observed_at,
        recorded_by=actor_id,
    )
    db.add(item)
    db.flush()
    _changed(ticket)
    _fact(db, ticket, actor_id, "manual_diagnostic_added", {"diagnostic_id": str(item.id)})
    db.commit()
    db.refresh(item)
    return item


def confirm_monitoring_check(
    db: Session, actor_id: UUID, ticket_id: UUID, check_id: UUID, version: int | None
) -> DiagnosticSnapshot:
    """Atomically acknowledge a server-produced report into immutable ticket history."""
    _actor(db, actor_id, Permission.TICKET_MANUAL_DIAGNOSTIC)
    ticket = _ticket(db, ticket_id, lock=True)
    _version(ticket, version)
    if ticket.status != TicketStatus.IN_PROGRESS:
        raise ApiError(409, "invalid_transition", "Start ticket before confirming diagnostics")
    check = db.execute(
        select(DiagnosticCheck).where(DiagnosticCheck.id == check_id).with_for_update()
    ).scalar_one_or_none()
    if check is None or check.ticket_id != ticket_id or check.requested_by != actor_id:
        raise ApiError(404, "check_not_found", "Diagnostic check not found")
    if check.expires_at <= datetime.now(UTC):
        raise ApiError(409, "check_expired", "Diagnostic check expired; run it again")
    if db.scalar(select(DiagnosticSnapshot.id).where(DiagnosticSnapshot.check_id == check_id)):
        raise ApiError(409, "check_already_confirmed", "Diagnostic check already confirmed")
    report = check.report
    item = DiagnosticSnapshot(
        ticket_id=ticket.id,
        subscriber_snapshot_id=ticket.subscriber_snapshot_id,
        connection_snapshot_id=check.connection_snapshot_id,
        check_id=check.id,
        source_kind="AUTOMATED",
        availability=(
            "KNOWN"
            if any(o.get("quality") == "KNOWN" for o in report["observations"])
            else "UNKNOWN"
        ),
        observation=report,
        observed_at=datetime.fromisoformat(report["checked_at"].replace("Z", "+00:00")),
        recorded_by=actor_id,
    )
    db.add(item)
    try:
        db.flush()
        _changed(ticket)
        _fact(
            db, ticket, actor_id, "automatic_diagnostic_confirmed", {"diagnostic_id": str(item.id)}
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise ApiError(
            409, "check_already_confirmed", "Diagnostic check already confirmed"
        ) from None
    db.refresh(item)
    return item


def add_decision(
    db: Session, actor_id: UUID, ticket_id: UUID, payload: AddDecision
) -> TicketResolution:
    _actor(db, actor_id, Permission.TICKET_DECISION)
    ticket = _ticket(db, ticket_id, lock=True)
    if ticket.status != TicketStatus.IN_PROGRESS:
        raise ApiError(409, "invalid_transition", "Start ticket before recording a decision")
    item = TicketResolution(
        ticket_id=ticket_id,
        engineer_id=actor_id,
        decision_text=payload.decision_text,
        performed_actions=payload.performed_actions,
    )
    db.add(item)
    db.flush()
    _changed(ticket)
    _fact(db, ticket, actor_id, "decision_recorded", {"decision_id": str(item.id)})
    db.commit()
    db.refresh(item)
    return item


def close_ticket(
    db: Session, actor_id: UUID, ticket_id: UUID, decision_id: UUID, version: int | None
) -> Ticket:
    _actor(db, actor_id, Permission.TICKET_CLOSE)
    ticket = _ticket(db, ticket_id, lock=True)
    _version(ticket, version)
    if ticket.status != TicketStatus.IN_PROGRESS:
        raise ApiError(409, "invalid_transition", "Only in-progress tickets can be closed")
    decision = db.get(TicketResolution, decision_id)
    if decision is None or decision.ticket_id != ticket_id:
        raise ApiError(422, "invalid_decision", "Decision must belong to ticket")
    ticket.status = TicketStatus.CLOSED
    ticket.closed_by = actor_id
    ticket.closed_at = datetime.now(UTC)
    ticket.closed_decision_id = decision_id
    _changed(ticket)
    _fact(db, ticket, actor_id, "ticket_closed", {"decision_id": str(decision_id)})
    db.commit()
    db.refresh(ticket)
    return ticket
