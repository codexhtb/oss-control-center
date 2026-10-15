"""Service Desk records and immutable source snapshots."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SubscriberSnapshot(Base):
    __tablename__ = "subscriber_snapshots"
    __table_args__ = (
        UniqueConstraint("id", "account_no", name="uq_subscriber_snapshots_id_account"),
        CheckConstraint("source IN ('MOCK', 'NETUP')", name="ck_subscriber_snapshots_source"),
        CheckConstraint("length(account_no) > 0", name="ck_subscriber_snapshots_account"),
        CheckConstraint("expires_at > fetched_at", name="ck_subscriber_snapshots_expiry"),
        Index("ix_subscriber_snapshots_account_fetched", "account_no", "fetched_at"),
        Index(
            "ix_subscriber_snapshots_source_subscriber",
            "source",
            "subscriber_id",
            "id",
            postgresql_where=text("subscriber_id IS NOT NULL"),
        ),
        Index("ix_subscriber_snapshots_fetched", "fetched_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    account_no: Mapped[str] = mapped_column(String(100), nullable=False)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT"), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(50))
    contacts: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    subscriber_id: Mapped[str | None] = mapped_column(String(100))
    organization_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(200))
    actual_address: Mapped[str | None] = mapped_column(String(300))
    accounts: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    financial_accounts: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    field_meta: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    contracts: Mapped[list] = mapped_column(JSONB, nullable=False)
    services: Mapped[list] = mapped_column(JSONB, nullable=False)


class ConnectionSnapshot(Base):
    __tablename__ = "connection_snapshots"
    __table_args__ = (
        UniqueConstraint("id", "subscriber_snapshot_id", name="uq_connection_snapshot_parent"),
        CheckConstraint(
            "access_type IN ('ETHERNET', 'EPON', 'GPON', 'UNKNOWN')",
            name="ck_connection_snapshots_access_type",
        ),
        CheckConstraint(
            "access_type <> 'ETHERNET' OR onu_id IS NULL",
            name="ck_connection_snapshots_ethernet_onu",
        ),
        Index("ix_connection_snapshots_parent", "subscriber_snapshot_id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    subscriber_snapshot_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("subscriber_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    access_type: Mapped[str] = mapped_column(String(10), nullable=False)
    device: Mapped[str | None] = mapped_column(String(200))
    port: Mapped[str | None] = mapped_column(String(100))
    vlan: Mapped[str | None] = mapped_column(String(100))
    onu_id: Mapped[str | None] = mapped_column(String(100))
    address: Mapped[str | None] = mapped_column(String(300))
    ip_group: Mapped[str | None] = mapped_column(String(100))
    service_id: Mapped[str | None] = mapped_column(String(100))
    ip_groups: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    field_meta: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        UniqueConstraint("id", "subscriber_snapshot_id", name="uq_tickets_snapshot"),
        ForeignKeyConstraint(
            ["subscriber_snapshot_id", "account_no"],
            ["subscriber_snapshots.id", "subscriber_snapshots.account_no"],
            ondelete="RESTRICT",
            name="fk_tickets_confirmed_account",
        ),
        ForeignKeyConstraint(
            ["connection_snapshot_id", "subscriber_snapshot_id"],
            ["connection_snapshots.id", "connection_snapshots.subscriber_snapshot_id"],
            ondelete="RESTRICT",
            name="fk_tickets_connection_parent",
        ),
        ForeignKeyConstraint(
            ["closed_decision_id", "id"],
            ["ticket_decisions.id", "ticket_decisions.ticket_id"],
            ondelete="RESTRICT",
            name="fk_tickets_closed_decision",
            use_alter=True,
        ),
        UniqueConstraint("created_by", "client_request_id", name="uq_tickets_creator_request"),
        CheckConstraint("status IN ('OPEN', 'IN_PROGRESS', 'CLOSED')", name="ck_tickets_status"),
        CheckConstraint(
            "priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')", name="ck_tickets_priority"
        ),
        CheckConstraint("version > 0", name="ck_tickets_version"),
        CheckConstraint(
            "(deleted_at IS NULL AND deleted_by IS NULL AND delete_reason IS NULL) OR "
            "(deleted_at IS NOT NULL AND deleted_by IS NOT NULL)",
            name="ck_tickets_delete_fields",
        ),
        CheckConstraint("length(description) > 0", name="ck_tickets_description"),
        CheckConstraint(
            "subject IS NULL OR (length(btrim(subject)) > 0 AND subject !~ '[[:cntrl:]]')",
            name="ck_tickets_subject",
        ),
        CheckConstraint(
            "(ticket_type = 'SUBSCRIBER_LINKED' AND subscriber_snapshot_id IS NOT NULL "
            "AND account_no IS NOT NULL AND length(account_no) > 0) OR "
            "(ticket_type IN ('MANUAL', 'INTERNAL') AND subscriber_snapshot_id IS NULL "
            "AND account_no IS NULL AND connection_snapshot_id IS NULL "
            "AND selected_service_id IS NULL)",
            name="ck_tickets_type_snapshot",
        ),
        CheckConstraint(
            "selected_service_id IS NULL OR length(selected_service_id) > 0",
            name="ck_tickets_selected_service_id",
        ),
        CheckConstraint(
            "(status = 'CLOSED' AND closed_by IS NOT NULL AND closed_at IS NOT NULL "
            "AND closed_decision_id IS NOT NULL) OR "
            "(status <> 'CLOSED' AND closed_by IS NULL AND closed_at IS NULL "
            "AND closed_decision_id IS NULL)",
            name="ck_tickets_close_fields",
        ),
        Index("ix_tickets_status_created", "status", "created_at", "id"),
        Index("ix_tickets_priority_created", "priority", "created_at"),
        Index("ix_tickets_assignee_status", "assigned_engineer_id", "status", "created_at"),
        Index("ix_tickets_account_created", "account_no", "created_at"),
        Index("ix_tickets_subscriber_snapshot", "subscriber_snapshot_id"),
        Index(
            "ix_tickets_deleted_at",
            "deleted_at",
            "id",
            postgresql_where=text("deleted_at IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    number: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True, nullable=False)
    ticket_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'SUBSCRIBER_LINKED'")
    )
    account_no: Mapped[str | None] = mapped_column(String(100), nullable=True)
    subscriber_snapshot_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("subscriber_snapshots.id", ondelete="RESTRICT"),
        nullable=True,
    )
    connection_snapshot_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    selected_service_id: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_engineer_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT")
    )
    closed_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT")
    )
    closed_decision_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT")
    )
    delete_reason: Mapped[str | None] = mapped_column(String(500))
    client_request_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TicketComment(Base):
    __tablename__ = "ticket_comments"
    __table_args__ = (
        CheckConstraint("length(body) > 0", name="ck_ticket_comments_body"),
        Index("ix_ticket_comments_ticket_created", "ticket_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tickets.id", ondelete="RESTRICT"), nullable=False
    )
    author_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TicketResolution(Base):
    __tablename__ = "ticket_decisions"
    __table_args__ = (
        UniqueConstraint("id", "ticket_id", name="uq_ticket_decisions_ticket"),
        CheckConstraint("length(decision_text) > 0", name="ck_ticket_decisions_text"),
        CheckConstraint("length(performed_actions) > 0", name="ck_ticket_decisions_actions"),
        Index("ix_ticket_decisions_ticket_created", "ticket_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tickets.id", ondelete="RESTRICT"), nullable=False
    )
    engineer_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT"), nullable=False
    )
    decision_text: Mapped[str] = mapped_column(Text, nullable=False)
    performed_actions: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TicketEvent(Base):
    __tablename__ = "ticket_history"
    __table_args__ = (
        Index("ix_ticket_history_ticket_occurred", "ticket_id", "occurred_at", "id"),
        Index("ix_ticket_history_recent", "occurred_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tickets.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT")
    )
    event_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ticket_version: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TicketAudit(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("origin IN ('API', 'SYSTEM')", name="ck_audit_events_origin"),
        Index("ix_audit_events_ticket_occurred", "ticket_id", "occurred_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tickets.id", ondelete="RESTRICT")
    )
    actor_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TicketPurgeAudit(Base):
    """Irreversible purge fact without a ticket FK or subscriber details."""

    __tablename__ = "ticket_purge_audit"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ticket_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    ticket_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    actor_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT"), nullable=False
    )
    purged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LookupCleanupAudit(Base):
    """Aggregate maintenance fact; never stores a lookup ID or subscriber details."""

    __tablename__ = "lookup_cleanup_audit"
    __table_args__ = (CheckConstraint("deleted_count >= 0", name="ck_lookup_cleanup_count"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ran_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_count: Mapped[int] = mapped_column(Integer, nullable=False)


class DiagnosticSnapshot(Base):
    __tablename__ = "diagnostic_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], ondelete="RESTRICT", name="fk_diagnostics_ticket"
        ),
        ForeignKeyConstraint(
            ["ticket_id", "subscriber_snapshot_id"],
            ["tickets.id", "tickets.subscriber_snapshot_id"],
            ondelete="RESTRICT",
            name="fk_diagnostics_ticket_snapshot",
        ),
        ForeignKeyConstraint(
            ["connection_snapshot_id", "subscriber_snapshot_id"],
            ["connection_snapshots.id", "connection_snapshots.subscriber_snapshot_id"],
            ondelete="RESTRICT",
            name="fk_diagnostics_connection_parent",
        ),
        CheckConstraint("source_kind IN ('MANUAL', 'AUTOMATED')", name="ck_diagnostics_source"),
        UniqueConstraint("check_id", name="uq_diagnostics_check"),
        CheckConstraint(
            "(source_kind = 'MANUAL' AND check_id IS NULL) OR "
            "(source_kind = 'AUTOMATED' AND check_id IS NOT NULL)",
            name="ck_diagnostics_check_source",
        ),
        CheckConstraint("availability IN ('KNOWN', 'UNKNOWN')", name="ck_diagnostics_availability"),
        CheckConstraint(
            "connection_snapshot_id IS NULL OR subscriber_snapshot_id IS NOT NULL",
            name="ck_diagnostics_connection_parent_required",
        ),
        Index("ix_diagnostics_ticket_recorded", "ticket_id", "recorded_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    check_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("diagnostic_checks.id", ondelete="RESTRICT"),
        nullable=True,
    )
    ticket_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    subscriber_snapshot_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    connection_snapshot_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    source_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="MANUAL")
    availability: Mapped[str] = mapped_column(String(16), nullable=False)
    observation: Mapped[dict] = mapped_column(JSONB, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    recorded_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_users.id", ondelete="RESTRICT"), nullable=False
    )


class DiagnosticCheck(Base):
    """Immutable unconfirmed result; confirmation creates one DiagnosticSnapshot."""

    __tablename__ = "diagnostic_checks"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ticket_id", "subscriber_snapshot_id"],
            ["tickets.id", "tickets.subscriber_snapshot_id"],
            ondelete="RESTRICT",
            name="fk_diagnostic_checks_ticket_snapshot",
        ),
        ForeignKeyConstraint(
            ["connection_snapshot_id", "subscriber_snapshot_id"],
            ["connection_snapshots.id", "connection_snapshots.subscriber_snapshot_id"],
            ondelete="RESTRICT",
            name="fk_diagnostic_checks_connection_parent",
        ),
        CheckConstraint("expires_at > created_at", name="ck_diagnostic_checks_expiry"),
        Index("ix_diagnostic_checks_ticket_created", "ticket_id", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    ticket_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tickets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    subscriber_snapshot_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    connection_snapshot_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    requested_by: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("app_users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    report: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
