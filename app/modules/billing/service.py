"""Persist normalized read-only billing observations as immutable snapshots."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.modules.billing.provider import (
    BillingAmbiguous,
    BillingIncomplete,
    BillingInvalidAccount,
    BillingProvider,
    BillingUnavailable,
    Subscriber360,
)
from app.modules.billing.schemas import ConnectionCard, SubscriberCard, SubscriberInfo
from app.modules.identity.models import User
from app.modules.identity.roles import Permission, Role, permissions_for
from app.modules.tickets.models import ConnectionSnapshot, SubscriberSnapshot, TicketAudit

LOOKUP_LIFETIME = timedelta(minutes=15)
MAX_ITEMS = 50
MAX_TOTAL_GROUPS = 200
MAX_TOTAL_GROUP_ROWS = 2000
MAX_TOTAL_RANGES = 5000
MAX_SERIALIZED_BYTES = 2_000_000


def validate_snapshot_structure(data: Subscriber360) -> None:
    """Shared writer invariant for normalized provider data before any DB insert."""
    account_ids = [item.account_id for item in data.accounts]
    service_ids = [item.service_id for item in data.services]
    if len(set(account_ids)) != len(account_ids) or len(set(service_ids)) != len(service_ids):
        raise ValueError("Duplicate account or service identity")
    if any(
        item.account_id is not None and item.account_id not in account_ids for item in data.services
    ):
        raise ValueError("Service references an absent account")
    for connection in data.connections:
        if connection.service_id is not None and connection.service_id not in service_ids:
            raise ValueError("Connection references an absent service")
        group_ids = [group.group_id for group in connection.ip_groups]
        if len(set(group_ids)) != len(group_ids):
            raise ValueError("Connection has duplicate group identity")
        for group in connection.ip_groups:
            row_ids = [row.row_id for row in group.rows]
            if not row_ids or len(set(row_ids)) != len(row_ids):
                raise ValueError("Group has no rows or duplicate row identity")


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
    # Release identity/session reads and the row lock before remote I/O. No PostgreSQL
    # transaction or pooled connection is held while the external source responds.
    db.rollback()
    try:
        data = provider.lookup_by_account(account_no)
    except BillingInvalidAccount:
        _audit(db, actor_id, "invalid")
        db.commit()
        raise ApiError(422, "account_format_unsupported", "Unsupported account format") from None
    except BillingAmbiguous:
        _audit(db, actor_id, "ambiguous")
        db.commit()
        raise ApiError(409, "billing_ambiguous", "Billing account is ambiguous") from None
    except BillingIncomplete:
        _audit(db, actor_id, "incomplete")
        db.commit()
        raise ApiError(409, "billing_incomplete", "Billing account data is incomplete") from None
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
    try:
        validate_snapshot_structure(data)
    except ValueError:
        _audit(db, actor_id, "invalid")
        db.commit()
        raise ApiError(
            503, "billing_invalid_response", "Billing source returned invalid structure"
        ) from None
    if (
        len(data.contracts) > MAX_ITEMS
        or len(data.accounts) > MAX_ITEMS
        or len(data.contacts) > 10
        or len(data.services) > MAX_ITEMS
        or len(data.connections) > MAX_ITEMS
        or sum(len(connection.ip_groups) for connection in data.connections) > MAX_TOTAL_GROUPS
        or sum(len(group.rows) for connection in data.connections for group in connection.ip_groups)
        > MAX_TOTAL_GROUP_ROWS
        or sum(
            len(row.dhcp_ranges)
            for connection in data.connections
            for group in connection.ip_groups
            for row in group.rows
        )
        > MAX_TOTAL_RANGES
        or any(
            len(connection.ip_groups) > MAX_ITEMS
            or any(len(group.rows) > 100 for group in connection.ip_groups)
            for connection in data.connections
        )
    ):
        _audit(db, actor_id, "invalid")
        db.commit()
        raise ApiError(503, "billing_invalid_response", "Billing source returned too many items")
    if len(data.model_dump_json().encode("utf-8")) > MAX_SERIALIZED_BYTES:
        _audit(db, actor_id, "invalid")
        db.commit()
        raise ApiError(503, "billing_invalid_response", "Billing source response is too large")
    # Identity may have been deactivated or re-roled while NetUP was queried.
    _actor(db, actor_id)
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
        contacts=[item.model_dump(mode="json") for item in data.contacts],
        email=data.email,
        actual_address=data.actual_address,
        accounts=[item.model_dump(mode="json") for item in data.accounts],
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
            service_id=item.service_id,
            ip_groups=[group.model_dump(mode="json") for group in item.ip_groups],
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
            contacts=snapshot.contacts,
            email=snapshot.email,
            actual_address=snapshot.actual_address,
            field_meta=snapshot.field_meta,
        ),
        contracts=snapshot.contracts,
        accounts=snapshot.accounts,
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
                service_id=item.service_id,
                ip_groups=item.ip_groups,
                onu_id=item.onu_id,
                field_meta=item.field_meta,
            )
            for item in connections
        ],
    )
