"""Administrator chat moderation with recoverable SSE events.

Revision ID: 0030_chat_moderation
Revises: 0029_staff_chat
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030_chat_moderation"
down_revision: str | None = "0029_staff_chat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSIONS = (
    "'users.manage','users.read','tickets.create','tickets.read.all',"
    "'tickets.read.own','tickets.edit.all','tickets.edit.own','tickets.comment',"
    "'tickets.assign','tickets.start','tickets.diagnose','tickets.resolve',"
    "'tickets.close','tickets.callback','subscribers.read','subscribers.finance.read',"
    "'diagnostics.read','diagnostics.run','audit.read','tickets.field_work',"
    "'tickets.delete.own','tickets.delete.all','tickets.restore','tickets.purge'"
)


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("chat_messages", sa.Column("deleted_by", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_chat_messages_deleted_by",
        "chat_messages",
        "app_users",
        ["deleted_by"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint("ck_chat_messages_body", "chat_messages", type_="check")
    op.create_check_constraint(
        "ck_chat_messages_body",
        "chat_messages",
        "(deleted_at IS NULL AND deleted_by IS NULL AND length(body) BETWEEN 1 AND 2000) "
        "OR (deleted_at IS NOT NULL AND deleted_by IS NOT NULL AND body = '')",
    )
    op.create_table(
        "chat_events",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("room_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", sa.BigInteger, nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["room_id"], ["chat_rooms.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["message_id"], ["chat_messages.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("message_id", "event_type", name="uq_chat_events_message_type"),
        sa.CheckConstraint("event_type IN ('MESSAGE', 'DELETE')", name="ck_chat_events_type"),
    )
    op.create_index("ix_chat_events_room_id", "chat_events", ["room_id", "id"])
    # Keep historical message IDs as SSE event IDs for open clients during rollout.
    op.execute(
        "INSERT INTO chat_events (id, room_id, message_id, event_type, created_at) "
        "SELECT id, room_id, id, 'MESSAGE', created_at FROM chat_messages ORDER BY id"
    )
    op.execute(
        "SELECT setval(pg_get_serial_sequence('chat_events', 'id'), "
        "greatest((SELECT coalesce(max(id), 0) FROM chat_events), 1), "
        "(SELECT count(*) > 0 FROM chat_events))"
    )
    op.drop_constraint("ck_app_user_permissions_known", "app_user_permissions", type_="check")
    op.create_check_constraint(
        "ck_app_user_permissions_known",
        "app_user_permissions",
        f"permission IN ({PERMISSIONS},'chat.messages.delete')",
    )
    op.execute(
        "INSERT INTO app_user_permissions (user_id, permission) "
        "SELECT id, 'chat.messages.delete' FROM app_users WHERE role = 'admin' "
        "ON CONFLICT DO NOTHING"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(
        sa.text("SELECT EXISTS(SELECT 1 FROM chat_messages WHERE deleted_at IS NOT NULL)")
    ):
        raise RuntimeError("Deleted chat messages cannot be restored")
    op.execute("DELETE FROM app_user_permissions WHERE permission = 'chat.messages.delete'")
    op.drop_constraint("ck_app_user_permissions_known", "app_user_permissions", type_="check")
    op.create_check_constraint(
        "ck_app_user_permissions_known",
        "app_user_permissions",
        f"permission IN ({PERMISSIONS})",
    )
    op.drop_index("ix_chat_events_room_id", table_name="chat_events")
    op.drop_table("chat_events")
    op.drop_constraint("ck_chat_messages_body", "chat_messages", type_="check")
    op.create_check_constraint(
        "ck_chat_messages_body", "chat_messages", "length(body) BETWEEN 1 AND 2000"
    )
    op.drop_constraint("fk_chat_messages_deleted_by", "chat_messages", type_="foreignkey")
    op.drop_column("chat_messages", "deleted_by")
    op.drop_column("chat_messages", "deleted_at")
