"""Persist each callback attempt and allow a resolved-ticket callback queue.

Revision ID: 0017_ticket_callbacks
Revises: 0016_ticket_resolved
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0017_ticket_callbacks"
down_revision: str | None = "0016_ticket_resolved"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_app_user_permissions_known", "app_user_permissions", type_="check")
    op.create_check_constraint(
        "ck_app_user_permissions_known", "app_user_permissions",
        "permission IN ("
        "'users.manage','users.read','tickets.create','tickets.read.all',"
        "'tickets.read.own','tickets.edit.all','tickets.edit.own','tickets.comment',"
        "'tickets.assign','tickets.start','tickets.diagnose','tickets.resolve',"
        "'tickets.close','tickets.callback','subscribers.read','subscribers.finance.read',"
        "'diagnostics.read','diagnostics.run','audit.read','tickets.field_work',"
        "'tickets.delete.own','tickets.delete.all','tickets.restore','tickets.purge')",
    )
    op.execute("""
        INSERT INTO app_user_permissions (user_id, permission)
        SELECT id, 'tickets.callback' FROM app_users WHERE role = 'admin'
        ON CONFLICT DO NOTHING
    """)
    op.create_table(
        "ticket_callbacks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("ticket_id", UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("client_request_id", UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", UUID(as_uuid=True), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("comment", sa.String(2000)),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["app_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decision_id", "ticket_id"],
                                ["ticket_decisions.id", "ticket_decisions.ticket_id"],
                                name="fk_ticket_callbacks_decision", ondelete="RESTRICT"),
        sa.UniqueConstraint("ticket_id", "client_request_id", name="uq_ticket_callbacks_request"),
        sa.CheckConstraint("outcome IN ('CONFIRMED', 'STILL_BROKEN', 'NO_ANSWER')",
                           name="ck_ticket_callbacks_outcome"),
        sa.CheckConstraint("outcome <> 'STILL_BROKEN' OR "
                           "(comment IS NOT NULL AND length(btrim(comment)) > 0)",
                           name="ck_ticket_callbacks_broken_comment"),
    )
    op.create_index("ix_ticket_callbacks_ticket_occurred", "ticket_callbacks",
                    ["ticket_id", "occurred_at", "id"])
    op.create_index("ix_tickets_callback_queue", "tickets", ["resolved_at", "id"],
                    postgresql_where=sa.text("status = 'RESOLVED' AND deleted_at IS NULL "
                                             "AND ticket_type = 'SUBSCRIBER_LINKED'"))
    op.execute("""
        CREATE FUNCTION ticket_callbacks_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               AND current_setting('oss.purge_ticket_id', true) = OLD.ticket_id::text
               AND EXISTS (SELECT 1 FROM tickets
                           WHERE id = OLD.ticket_id AND deleted_at IS NOT NULL)
               AND EXISTS (
                   SELECT 1 FROM app_users u WHERE u.id =
                       nullif(current_setting('oss.purge_actor_id', true), '')::uuid
                     AND u.is_active AND (
                         (NOT u.permissions_customized AND u.role = 'admin')
                         OR EXISTS (SELECT 1 FROM app_user_permissions p
                                    WHERE p.user_id = u.id AND p.permission = 'tickets.purge')
                     )
               ) THEN RETURN OLD; END IF;
            RAISE EXCEPTION USING MESSAGE = 'immutable callback record', ERRCODE = '23514';
        END $$
    """)
    op.execute("""
        CREATE TRIGGER immutable_ticket_callbacks BEFORE UPDATE OR DELETE ON ticket_callbacks
        FOR EACH ROW EXECUTE FUNCTION ticket_callbacks_immutable()
    """)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM ticket_callbacks)")):
        raise RuntimeError("Callback history cannot be discarded")
    op.execute("DROP TRIGGER immutable_ticket_callbacks ON ticket_callbacks")
    op.execute("DROP FUNCTION ticket_callbacks_immutable()")
    op.drop_index("ix_tickets_callback_queue", table_name="tickets")
    op.drop_index("ix_ticket_callbacks_ticket_occurred", table_name="ticket_callbacks")
    op.drop_table("ticket_callbacks")
    op.execute("DELETE FROM app_user_permissions WHERE permission = 'tickets.callback'")
    op.drop_constraint("ck_app_user_permissions_known", "app_user_permissions", type_="check")
    op.create_check_constraint(
        "ck_app_user_permissions_known", "app_user_permissions",
        "permission IN ("
        "'users.manage','users.read','tickets.create','tickets.read.all',"
        "'tickets.read.own','tickets.edit.all','tickets.edit.own','tickets.comment',"
        "'tickets.assign','tickets.start','tickets.diagnose','tickets.resolve',"
        "'tickets.close','subscribers.read','subscribers.finance.read',"
        "'diagnostics.read','diagnostics.run','audit.read','tickets.field_work',"
        "'tickets.delete.own','tickets.delete.all','tickets.restore','tickets.purge')",
    )
