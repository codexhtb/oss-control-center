"""Service Desk snapshots, tickets, history, resolutions, audit and manual diagnostics.

Revision ID: 0003_ticket_engine
Revises: 0002_identity_rbac
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_ticket_engine"
down_revision: str | None = "0002_identity_rbac"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def uuid(name: str, *, nullable: bool = False, primary_key: bool = False) -> sa.Column:
    return sa.Column(
        name, postgresql.UUID(as_uuid=True), nullable=nullable, primary_key=primary_key
    )


def timestamp(name: str, *, nullable: bool = False, default: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default else None,
    )


def upgrade() -> None:
    op.create_table(
        "subscriber_snapshots",
        uuid("id", primary_key=True),
        sa.Column("account_no", sa.String(100), nullable=False),
        sa.Column("source", sa.String(10), nullable=False),
        timestamp("fetched_at"),
        uuid("requested_by"),
        timestamp("expires_at"),
        sa.Column("full_name", sa.String(200)),
        sa.Column("phone", sa.String(50)),
        sa.Column("contracts", postgresql.JSONB, nullable=False),
        sa.Column("services", postgresql.JSONB, nullable=False),
        sa.ForeignKeyConstraint(["requested_by"], ["app_users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("source IN ('MOCK', 'NETUP')", name="ck_subscriber_snapshots_source"),
        sa.CheckConstraint("length(account_no) > 0", name="ck_subscriber_snapshots_account"),
        sa.CheckConstraint("expires_at > fetched_at", name="ck_subscriber_snapshots_expiry"),
    )
    op.create_index(
        "ix_subscriber_snapshots_account_fetched",
        "subscriber_snapshots",
        ["account_no", "fetched_at"],
    )
    op.create_table(
        "connection_snapshots",
        uuid("id", primary_key=True),
        uuid("subscriber_snapshot_id"),
        sa.Column("access_type", sa.String(10), nullable=False),
        sa.Column("device", sa.String(200)),
        sa.Column("port", sa.String(100)),
        sa.Column("vlan", sa.String(100)),
        sa.Column("onu_id", sa.String(100)),
        sa.ForeignKeyConstraint(
            ["subscriber_snapshot_id"], ["subscriber_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint("id", "subscriber_snapshot_id", name="uq_connection_snapshot_parent"),
        sa.CheckConstraint(
            "access_type IN ('ETHERNET', 'EPON', 'GPON', 'UNKNOWN')",
            name="ck_connection_snapshots_access_type",
        ),
        sa.CheckConstraint(
            "access_type <> 'ETHERNET' OR onu_id IS NULL",
            name="ck_connection_snapshots_ethernet_onu",
        ),
    )
    op.create_index(
        "ix_connection_snapshots_parent", "connection_snapshots", ["subscriber_snapshot_id"]
    )
    op.create_table(
        "tickets",
        uuid("id", primary_key=True),
        sa.Column("number", sa.BigInteger(), sa.Identity(), nullable=False, unique=True),
        sa.Column("account_no", sa.String(100), nullable=False),
        uuid("subscriber_snapshot_id"),
        uuid("connection_snapshot_id", nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        uuid("created_by"),
        uuid("assigned_engineer_id", nullable=True),
        uuid("closed_by", nullable=True),
        uuid("closed_decision_id", nullable=True),
        timestamp("closed_at", nullable=True),
        uuid("client_request_id"),
        sa.Column("version", sa.Integer(), nullable=False),
        timestamp("created_at", default=True),
        timestamp("updated_at", default=True),
        sa.ForeignKeyConstraint(
            ["subscriber_snapshot_id"], ["subscriber_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["app_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_engineer_id"], ["app_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["closed_by"], ["app_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["connection_snapshot_id", "subscriber_snapshot_id"],
            ["connection_snapshots.id", "connection_snapshots.subscriber_snapshot_id"],
            ondelete="RESTRICT",
            name="fk_tickets_connection_parent",
        ),
        sa.UniqueConstraint("id", "subscriber_snapshot_id", name="uq_tickets_snapshot"),
        sa.UniqueConstraint("created_by", "client_request_id", name="uq_tickets_creator_request"),
        sa.CheckConstraint("status IN ('OPEN', 'IN_PROGRESS', 'CLOSED')", name="ck_tickets_status"),
        sa.CheckConstraint(
            "priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')", name="ck_tickets_priority"
        ),
        sa.CheckConstraint("version > 0", name="ck_tickets_version"),
        sa.CheckConstraint("length(description) > 0", name="ck_tickets_description"),
        sa.CheckConstraint(
            "(status = 'CLOSED' AND closed_by IS NOT NULL AND closed_at IS NOT NULL "
            "AND closed_decision_id IS NOT NULL) OR "
            "(status <> 'CLOSED' AND closed_by IS NULL AND closed_at IS NULL "
            "AND closed_decision_id IS NULL)",
            name="ck_tickets_close_fields",
        ),
    )
    op.create_index("ix_tickets_status_created", "tickets", ["status", "created_at", "id"])
    op.create_index("ix_tickets_priority_created", "tickets", ["priority", "created_at"])
    op.create_index(
        "ix_tickets_assignee_status", "tickets", ["assigned_engineer_id", "status", "created_at"]
    )
    op.create_index("ix_tickets_account_created", "tickets", ["account_no", "created_at"])
    op.create_table(
        "ticket_comments",
        uuid("id", primary_key=True),
        uuid("ticket_id"),
        uuid("author_id"),
        sa.Column("body", sa.Text(), nullable=False),
        timestamp("created_at", default=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["author_id"], ["app_users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("length(body) > 0", name="ck_ticket_comments_body"),
    )
    op.create_index(
        "ix_ticket_comments_ticket_created", "ticket_comments", ["ticket_id", "created_at", "id"]
    )
    op.create_table(
        "ticket_decisions",
        uuid("id", primary_key=True),
        uuid("ticket_id"),
        uuid("engineer_id"),
        sa.Column("decision_text", sa.Text(), nullable=False),
        sa.Column("performed_actions", sa.Text(), nullable=False),
        timestamp("created_at", default=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["engineer_id"], ["app_users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("id", "ticket_id", name="uq_ticket_decisions_ticket"),
        sa.CheckConstraint("length(decision_text) > 0", name="ck_ticket_decisions_text"),
        sa.CheckConstraint("length(performed_actions) > 0", name="ck_ticket_decisions_actions"),
    )
    op.create_index(
        "ix_ticket_decisions_ticket_created", "ticket_decisions", ["ticket_id", "created_at", "id"]
    )
    op.create_foreign_key(
        "fk_tickets_closed_decision",
        "tickets",
        "ticket_decisions",
        ["closed_decision_id", "id"],
        ["id", "ticket_id"],
        ondelete="RESTRICT",
    )
    op.create_table(
        "ticket_history",
        uuid("id", primary_key=True),
        uuid("ticket_id"),
        sa.Column("event_type", sa.String(40), nullable=False),
        uuid("actor_id", nullable=True),
        sa.Column("event_data", postgresql.JSONB, nullable=False),
        sa.Column("ticket_version", sa.Integer(), nullable=False),
        timestamp("occurred_at", default=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["app_users.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "ix_ticket_history_ticket_occurred", "ticket_history", ["ticket_id", "occurred_at", "id"]
    )
    op.create_table(
        "audit_events",
        uuid("id", primary_key=True),
        uuid("ticket_id", nullable=True),
        uuid("actor_id", nullable=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        timestamp("occurred_at", default=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["app_users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("origin IN ('API', 'SYSTEM')", name="ck_audit_events_origin"),
    )
    op.create_index(
        "ix_audit_events_ticket_occurred", "audit_events", ["ticket_id", "occurred_at", "id"]
    )
    op.create_table(
        "diagnostic_snapshots",
        uuid("id", primary_key=True),
        uuid("ticket_id"),
        uuid("subscriber_snapshot_id"),
        uuid("connection_snapshot_id", nullable=True),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("availability", sa.String(16), nullable=False),
        sa.Column("observation", postgresql.JSONB, nullable=False),
        timestamp("observed_at"),
        timestamp("recorded_at", default=True),
        uuid("recorded_by"),
        sa.ForeignKeyConstraint(
            ["ticket_id", "subscriber_snapshot_id"],
            ["tickets.id", "tickets.subscriber_snapshot_id"],
            ondelete="RESTRICT",
            name="fk_diagnostics_ticket_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["connection_snapshot_id", "subscriber_snapshot_id"],
            ["connection_snapshots.id", "connection_snapshots.subscriber_snapshot_id"],
            ondelete="RESTRICT",
            name="fk_diagnostics_connection_parent",
        ),
        sa.ForeignKeyConstraint(["recorded_by"], ["app_users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("source_kind = 'MANUAL'", name="ck_diagnostics_phase1_source"),
        sa.CheckConstraint(
            "availability IN ('KNOWN', 'UNKNOWN')", name="ck_diagnostics_availability"
        ),
    )
    op.create_index(
        "ix_diagnostics_ticket_recorded", "diagnostic_snapshots", ["ticket_id", "recorded_at", "id"]
    )
    op.execute(
        """CREATE FUNCTION ticket_records_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION USING MESSAGE = 'immutable Service Desk record', ERRCODE = '23514';
        END $$"""
    )
    for table in (
        "subscriber_snapshots",
        "connection_snapshots",
        "ticket_comments",
        "ticket_decisions",
        "ticket_history",
        "audit_events",
        "diagnostic_snapshots",
    ):
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION ticket_records_immutable()"
        )
    op.execute(
        """CREATE TRIGGER no_delete_tickets BEFORE DELETE ON tickets
        FOR EACH ROW EXECUTE FUNCTION ticket_records_immutable()"""
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER no_delete_tickets ON tickets")
    for table in (
        "diagnostic_snapshots",
        "audit_events",
        "ticket_history",
        "ticket_decisions",
        "ticket_comments",
        "connection_snapshots",
        "subscriber_snapshots",
    ):
        op.execute(f"DROP TRIGGER immutable_{table} ON {table}")
    op.execute("DROP FUNCTION ticket_records_immutable()")
    op.drop_table("diagnostic_snapshots")
    op.drop_table("audit_events")
    op.drop_table("ticket_history")
    op.drop_constraint("fk_tickets_closed_decision", "tickets", type_="foreignkey")
    op.drop_table("ticket_decisions")
    op.drop_table("ticket_comments")
    op.drop_table("tickets")
    op.drop_table("connection_snapshots")
    op.drop_table("subscriber_snapshots")
