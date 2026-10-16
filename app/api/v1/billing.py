"""Subscriber 360 routes; mock by default, NetUP only by explicit configuration."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select

from app.api.v1.identity import CsrfIdentityDep, CurrentIdentityDep, SessionDep
from app.core.config import get_settings
from app.core.errors import ApiError
from app.modules.billing.mock import MockBillingProvider
from app.modules.billing.netup.config import NetupConfig
from app.modules.billing.netup.provider import NetUPBillingProvider
from app.modules.billing.provider import BillingProvider, UnavailableBillingProvider
from app.modules.billing.schemas import LookupRequest, SubscriberCard
from app.modules.billing.service import create_lookup, stored_card
from app.modules.identity.access import has_permission
from app.modules.identity.models import User
from app.modules.identity.roles import Permission
from app.modules.identity.service import AuthContext, audit
from app.modules.tickets.duplicates import active_count, active_query
from app.modules.tickets.models import SubscriberSnapshot, Ticket
from app.modules.tickets.schemas import ActiveTickets, Page, TicketView

router = APIRouter(prefix="/subscriber-lookups", tags=["billing"])


def get_billing_provider() -> BillingProvider:
    if get_settings().billing_provider == "NETUP":
        try:
            return NetUPBillingProvider.from_config(
                NetupConfig.from_env(app_env=get_settings().app_env)
            )
        except ValueError:
            return UnavailableBillingProvider()
    return MockBillingProvider()


ProviderDep = Annotated[BillingProvider, Depends(get_billing_provider)]


def _allowed(identity: AuthContext, db: SessionDep) -> AuthContext:
    if not has_permission(db, identity.user, Permission.SUBSCRIBER_READ):
        audit(db, "permission_denied", "denied", actor=identity.user.id)
        db.commit()
        raise ApiError(403, "forbidden", "Permission denied")
    return identity


def can_lookup(identity: CsrfIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db)


def can_read_lookup(identity: CurrentIdentityDep, db: SessionDep) -> AuthContext:
    return _allowed(identity, db)


LookupDep = Annotated[AuthContext, Depends(can_lookup)]
LookupReadDep = Annotated[AuthContext, Depends(can_read_lookup)]


@router.post("", response_model=SubscriberCard)
def lookup(
    payload: LookupRequest,
    response: Response,
    identity: LookupDep,
    db: SessionDep,
    provider: ProviderDep,
) -> SubscriberCard:
    snapshot = create_lookup(db, identity.user.id, payload.account_no, provider)
    response.headers["Cache-Control"] = "no-store"
    return stored_card(db, identity.user.id, snapshot.id)


@router.get("/{lookup_id}", response_model=SubscriberCard)
def get_lookup(
    lookup_id: UUID, response: Response, identity: LookupReadDep, db: SessionDep
) -> SubscriberCard:
    response.headers["Cache-Control"] = "no-store"
    return stored_card(db, identity.user.id, lookup_id)


@router.get("/{lookup_id}/tickets", response_model=Page[TicketView])
def subscriber_tickets(
    lookup_id: UUID,
    response: Response,
    identity: LookupReadDep,
    db: SessionDep,
    page: int = 1,
    size: int = 20,
) -> dict:
    if page < 1 or size < 1 or size > 50:
        raise ApiError(422, "invalid_page", "Invalid page or page size")
    if not (
        has_permission(db, identity.user, Permission.TICKET_READ)
        or has_permission(db, identity.user, Permission.TICKET_READ_OWN)
    ):
        raise ApiError(403, "forbidden", "Permission denied")
    snapshot = db.get(SubscriberSnapshot, lookup_id)
    if snapshot is None or snapshot.requested_by != identity.user.id:
        raise ApiError(404, "lookup_not_found", "Subscriber lookup not found")
    same_subscriber = (
        SubscriberSnapshot.subscriber_id == snapshot.subscriber_id
        if snapshot.subscriber_id is not None
        else SubscriberSnapshot.account_no == snapshot.account_no
    )
    conditions = (
        SubscriberSnapshot.source == snapshot.source,
        same_subscriber,
        Ticket.deleted_at.is_(None),
    )
    if not has_permission(db, identity.user, Permission.TICKET_READ):
        conditions += (Ticket.created_by == identity.user.id,)
    base = (
        select(Ticket)
        .join(SubscriberSnapshot, Ticket.subscriber_snapshot_id == SubscriberSnapshot.id)
        .where(*conditions)
    )
    total = (
        db.scalar(
            select(func.count())
            .select_from(Ticket)
            .join(SubscriberSnapshot, Ticket.subscriber_snapshot_id == SubscriberSnapshot.id)
            .where(*conditions)
        )
        or 0
    )
    items = db.scalars(
        base.order_by(Ticket.created_at.desc(), Ticket.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    response.headers["Cache-Control"] = "no-store"
    return {"items": items, "page": page, "size": size, "total": total}


@router.get("/{lookup_id}/active-tickets", response_model=ActiveTickets)
def active_tickets(
    lookup_id: UUID,
    response: Response,
    identity: LookupReadDep,
    db: SessionDep,
    page: int = 1,
    size: int = 20,
) -> dict:
    if page < 1 or page > 1000 or size < 1 or size > 50:
        raise ApiError(422, "invalid_page", "Invalid page or page size")
    snapshot = db.get(SubscriberSnapshot, lookup_id)
    if snapshot is None or snapshot.requested_by != identity.user.id:
        raise ApiError(404, "lookup_not_found", "Subscriber lookup not found")
    base = active_query(snapshot)
    total_active = active_count(db, snapshot)
    can_read_all = has_permission(db, identity.user, Permission.TICKET_READ)
    can_read_own = has_permission(db, identity.user, Permission.TICKET_READ_OWN)
    visible = base if can_read_all else base.where(Ticket.created_by == identity.user.id)
    total_visible = (
        db.scalar(select(func.count()).select_from(visible.subquery())) or 0
        if can_read_all or can_read_own
        else 0
    )
    items = (
        db.scalars(
            visible.order_by(Ticket.created_at.desc(), Ticket.id.desc())
            .offset((page - 1) * size)
            .limit(size)
        ).all()
        if can_read_all or can_read_own
        else []
    )
    engineer_ids = {item.assigned_engineer_id for item in items if item.assigned_engineer_id}
    names = (
        dict(db.execute(select(User.id, User.display_name).where(User.id.in_(engineer_ids))).all())
        if engineer_ids
        else {}
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "items": [
            {
                "id": item.id,
                "number": item.number,
                "subject": item.subject,
                "status": item.status,
                "created_at": item.created_at,
                "assigned_engineer_name": names.get(item.assigned_engineer_id),
                "description": item.description[:240],
            }
            for item in items
        ],
        "page": page,
        "size": size,
        "total_visible": total_visible,
        "has_hidden_active": total_active > total_visible,
    }
