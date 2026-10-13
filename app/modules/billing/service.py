"""Persist normalized read-only billing observations as immutable snapshots."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.billing.provider import BillingProvider, BillingUnavailable, Subscriber360
from app.modules.billing.schemas import ConnectionCard, SubscriberCard, SubscriberInfo
from app.modules.identity.models import User
from app.modules.identity.roles import Permission, Role, permissions_for
from app.modules.tickets.models import ConnectionSnapshot, SubscriberSnapshot, TicketAudit

LOOKUP_LIFETIME = timedelta(minutes=15)
MAX_ITEMS = 50


def _actor(db: Session, actor_id: UUID) -> None:
    user = db.execute(
        select(User)
        .where(User.id == actor_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (
        not user
        or not user.is_active
        or Permission.SUBSCRIBER_READ not in permissions_for(Role(user.role))
    ):
        raise ApiError(403, "forbidden", "Permission denied")


def _audit(db: Session, actor_id: UUID, outcome: str) -> None:
    db.add(
        TicketAudit(actor_id=actor_id, action="subscriber_lookup", outcome=outcome, origin="API")
    )


def create_lookup(
    db: Session, actor_id: UUID, account_no: str, provider: BillingProvider
) -> SubscriberSnapshot:
    _actor(db, actor_id)
    try:
        data = provider.lookup_by_account(account_no)
    except BillingUnavailable:
        _audit(db, actor_id, "unavailable")
        db.commit()
        raise ApiError(503, "billing_unavailable", "Billing source UNAVAILABLE") from None
    except ValueError:
        _audit(db, actor_id, "invalid")
        db.commit()
        raise ApiError(
            503, "billing_invalid_response", "Billing source returned invalid data"
        ) from None
    if data is None:
        _audit(db, actor_id, "not_found")
        db.commit()
        raise ApiError(404, "subscriber_not_found", "Subscriber not found")
    try:
        data = Subscriber360.model_validate(data)
    except ValueError:
        _audit(db, actor_id, "invalid")
        db.commit()
        raise ApiError(
            503, "billing_invalid_response", "Billing source returned invalid data"
        ) from None
    if data.billing_account != account_no:
        _audit(db, actor_id, "invalid")
        db.commit()
        raise ApiError(503, "billing_invalid_response", "Billing source returned invalid data")
    if (
        len(data.contracts) > MAX_ITEMS
        or len(data.services) > MAX_ITEMS
        or len(data.connections) > MAX_ITEMS
    ):
        _audit(db, actor_id, "invalid")
        db.commit()
        raise ApiError(503, "billing_invalid_response", "Billing source returned too many items")
    snapshot = SubscriberSnapshot(
        account_no=data.billing_account,
        source=data.source,
        fetched_at=data.fetched_at,
        expires_at=data.fetched_at + LOOKUP_LIFETIME,
        requested_by=actor_id,
        subscriber_id=data.subscriber_id,
        full_name=data.full_name,
        organization_name=data.organization_name,
        phone=data.phone,
        email=data.email,
        contracts=[item.model_dump(mode="json") for item in data.contracts],
        services=[item.model_dump(mode="json") for item in data.services],
        field_meta={key: value.model_dump(mode="json") for key, value in data.field_meta.items()},
    )
    db.add(snapshot)
    db.flush()
    db.add_all(
        ConnectionSnapshot(
            subscriber_snapshot_id=snapshot.id,
            access_type=item.access_type,
            address=item.address,
            device=item.device,
            port=item.port,
            vlan=item.vlan,
            ip_group=item.ip_group,
            onu_id=item.onu_id,
            field_meta={
                key: value.model_dump(mode="json") for key, value in item.field_meta.items()
            },
        )
        for item in data.connections
    )
    _audit(db, actor_id, "success")
    db.commit()
    return snapshot


def stored_card(db: Session, actor_id: UUID, lookup_id: UUID) -> SubscriberCard:
    _actor(db, actor_id)
    snapshot = db.get(SubscriberSnapshot, lookup_id)
    if snapshot is None or snapshot.requested_by != actor_id:
        raise ApiError(404, "lookup_not_found", "Subscriber lookup not found")
    connections = db.scalars(
        select(ConnectionSnapshot)
        .where(ConnectionSnapshot.subscriber_snapshot_id == lookup_id)
        .order_by(ConnectionSnapshot.id)
    ).all()
    return SubscriberCard(
        lookup_id=snapshot.id,
        source=snapshot.source,
        fetched_at=snapshot.fetched_at,
        expires_at=snapshot.expires_at,
        is_expired=snapshot.expires_at <= datetime.now(UTC),
        subscriber=SubscriberInfo(
            subscriber_id=snapshot.subscriber_id,
            billing_account=snapshot.account_no,
            full_name=snapshot.full_name,
            organization_name=snapshot.organization_name,
            phone=snapshot.phone,
            email=snapshot.email,
            field_meta=snapshot.field_meta,
        ),
        contracts=snapshot.contracts,
        services=snapshot.services,
        connections=[
            ConnectionCard(
                connection_snapshot_id=item.id,
                access_type=item.access_type,
                address=item.address,
                device=item.device,
                port=item.port,
                vlan=item.vlan,
                ip_group=item.ip_group,
                onu_id=item.onu_id,
                field_meta=item.field_meta,
            )
            for item in connections
        ],
    )
