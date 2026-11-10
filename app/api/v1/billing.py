"""Subscriber 360 routes; mock by default, NetUP only by explicit configuration."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select

from app.api.v1.identity import CsrfIdentityDep, CurrentIdentityDep, SessionDep
from app.core.config import get_settings
from app.core.errors import ApiError
from app.modules.billing.mock import MockBillingProvider
from app.modules.billing.netup.config import NetupConfig
from app.modules.billing.netup.finance import load_finance
from app.modules.billing.netup.provider import NetUPBillingProvider
from app.modules.billing.netup.repository import parse_account_no
from app.modules.billing.netup.services import load_services, load_tariff_history
from app.modules.billing.provider import (
    BillingIncomplete,
    BillingInvalidAccount,
    BillingProvider,
    BillingUnavailable,
    SubscriberSearchResult,
    UnavailableBillingProvider,
)
from app.modules.billing.schemas import LookupRequest, SubscriberCard
from app.modules.billing.service import create_lookup, stored_card
from app.modules.identity.access import has_permission
from app.modules.identity.models import User
from app.modules.identity.roles import Permission
from app.modules.identity.service import AuthContext, audit
from app.modules.tickets.duplicates import active_count, active_query
from app.modules.tickets.models import SubscriberSnapshot, Ticket, TicketAudit
from app.modules.tickets.schemas import ActiveTickets, Page, TicketView
from app.modules.tickets.subscriber_history import matching_subscriber

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


def _service_source(
    lookup_id: UUID, identity: AuthContext, db: SessionDep
) -> tuple[int, int, str | None]:
    if not has_permission(db, identity.user, Permission.SUBSCRIBER_FINANCE_READ):
        raise ApiError(403, "forbidden", "Permission denied")
    snapshot = db.get(SubscriberSnapshot, lookup_id)
    if snapshot is None or snapshot.requested_by != identity.user.id:
        raise ApiError(404, "lookup_not_found", "Subscriber lookup not found")
    if snapshot.source != "NETUP" or get_settings().billing_provider != "NETUP":
        raise ApiError(503, "services_unavailable", "NetUP services are unavailable")
    try:
        account_id = parse_account_no(snapshot.account_no)
        user_id = parse_account_no(snapshot.subscriber_id or "")
    except BillingInvalidAccount:
        raise ApiError(422, "account_format_unsupported", "Unsupported account format") from None
    currency = None
    for item in snapshot.financial_accounts:
        if item.get("account_id") == snapshot.account_no:
            meta = item.get("field_meta", {}).get("currency", {})
            if meta.get("quality") in ("KNOWN", "STALE"):
                currency = item.get("currency")
            break
    return account_id, user_id, currency


@router.get("/{lookup_id}/services")
def configured_services(
    lookup_id: UUID, response: Response, identity: LookupReadDep, db: SessionDep
) -> dict:
    account_id, user_id, currency = _service_source(lookup_id, identity, db)
    db.rollback()
    try:
        result = load_services(
            NetupConfig.from_env(app_env=get_settings().app_env), account_id, user_id, currency
        )
    except Exception:
        raise ApiError(503, "services_unavailable", "NetUP services are unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/{lookup_id}/tariff-history")
def tariff_history(
    lookup_id: UUID, response: Response, identity: LookupReadDep, db: SessionDep
) -> dict:
    account_id, _, _ = _service_source(lookup_id, identity, db)
    db.rollback()
    try:
        result = load_tariff_history(
            NetupConfig.from_env(app_env=get_settings().app_env), account_id
        )
    except Exception:
        raise ApiError(503, "services_unavailable", "NetUP tariff history is unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/{lookup_id}/finance")
def finance_history(
    lookup_id: UUID,
    response: Response,
    identity: LookupReadDep,
    db: SessionDep,
    start: datetime,
    end: datetime,
    kind: Literal["payments", "charges", "credit"] = "payments",
    page: int = 1,
    size: int = 30,
    service_link_id: int | None = None,
) -> dict:
    if not has_permission(db, identity.user, Permission.SUBSCRIBER_FINANCE_READ):
        raise ApiError(403, "forbidden", "Permission denied")
    snapshot = db.get(SubscriberSnapshot, lookup_id)
    if snapshot is None or snapshot.requested_by != identity.user.id:
        raise ApiError(404, "lookup_not_found", "Subscriber lookup not found")
    if snapshot.source != "NETUP" or get_settings().billing_provider != "NETUP":
        raise ApiError(503, "finance_unavailable", "Financial history is unavailable")
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or start >= end
        or end - start > timedelta(days=366)
        or end > datetime.now(UTC) + timedelta(days=1)
        or page < 1
        or page > 1000
        or size < 1
        or size > 50
        or (service_link_id is not None and (service_link_id < 1 or kind != "charges"))
    ):
        raise ApiError(422, "invalid_finance_range", "Invalid financial history range")
    try:
        account_id = parse_account_no(snapshot.account_no)
    except BillingInvalidAccount:
        raise ApiError(422, "account_format_unsupported", "Unsupported account format") from None
    currency = None
    for item in snapshot.financial_accounts:
        if item.get("account_id") == snapshot.account_no:
            meta = item.get("field_meta", {}).get("currency", {})
            if meta.get("quality") in ("KNOWN", "STALE"):
                currency = item.get("currency")
            break
    # Do not hold a PostgreSQL transaction or pooled connection during external I/O.
    db.rollback()
    try:
        result = load_finance(
            NetupConfig.from_env(app_env=get_settings().app_env),
            account_id,
            start.astimezone(UTC),
            end.astimezone(UTC),
            kind,
            page,
            size,
            currency,
            service_link_id,
        )
    except Exception:
        raise ApiError(503, "finance_unavailable", "Financial history is unavailable") from None
    response.headers["Cache-Control"] = "no-store"
    return result


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


@router.get("/search", response_model=SubscriberSearchResult)
def search_subscribers(
    q: str, response: Response, identity: LookupReadDep, db: SessionDep, provider: ProviderDep
) -> SubscriberSearchResult:
    query = " ".join(q.split())
    if not query or len(query) > 100 or any(ord(char) < 32 for char in q):
        raise ApiError(422, "subscriber_search_invalid", "Invalid subscriber search")
    db.rollback()  # Do not hold a PostgreSQL connection during the NetUP read.
    try:
        result = provider.search_subscribers(query)
    except (BillingUnavailable, BillingIncomplete):
        db.add(TicketAudit(
            actor_id=identity.user.id, action="subscriber_search",
            outcome="unavailable", origin="API",
        ))
        db.commit()
        raise ApiError(503, "billing_unavailable", "Billing source UNAVAILABLE") from None
    db.add(TicketAudit(
        actor_id=identity.user.id, action="subscriber_search", outcome="success", origin="API",
    ))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


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
    conditions = (
        matching_subscriber(snapshot),
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
