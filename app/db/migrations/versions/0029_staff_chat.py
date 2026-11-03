"""Persistent staff room, messages, and individual read cursors.

Revision ID: 0029_staff_chat
Revises: 0028_field_visit_address
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0029_staff_chat"
down_revision: str | None = "0028_field_visit_address"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAFF_ROOM_ID = "00000000-0000-4000-8000-000000000001"


def upgrade() -> None:
    op.create_table(
        "chat_rooms",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('STAFF', 'DIRECT')", name="ck_chat_rooms_kind"),
    )
    op.execute(
        f"INSERT INTO chat_rooms (id, kind, slug) VALUES ('{STAFF_ROOM_ID}', 'STAFF', 'staff')"
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("room_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sender_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sender_name", sa.String(150), nullable=False),
        sa.Column("client_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.String(2000), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["room_id"], ["chat_rooms.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["sender_id"], ["app_users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("room_id", "sender_id", "client_id", name="uq_chat_messages_client"),
        sa.CheckConstraint("length(body) BETWEEN 1 AND 2000", name="ck_chat_messages_body"),
    )
    op.create_index("ix_chat_messages_room_id", "chat_messages", ["room_id", "id"])
    op.create_index("ix_chat_messages_rate", "chat_messages", ["sender_id", "created_at"])
    op.create_table(
        "chat_reads",
        sa.Column("room_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_message_id", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["room_id"], ["chat_rooms.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["app_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("room_id", "user_id"),
        sa.CheckConstraint("last_message_id >= 0", name="ck_chat_reads_cursor"),
    )


def downgrade() -> None:
    op.drop_table("chat_reads")
    op.drop_index("ix_chat_messages_rate", table_name="chat_messages")
    op.drop_index("ix_chat_messages_room_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_table("chat_rooms")
