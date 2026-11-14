"""Link staff chat replies to a message in the same room.

Revision ID: 0044_chat_replies
Revises: 0043_internal_tasks
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_chat_replies"
down_revision: str | None = "0043_internal_tasks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("reply_to_message_id", sa.BigInteger()))
    op.create_foreign_key(
        "fk_chat_messages_reply_to",
        "chat_messages",
        "chat_messages",
        ["reply_to_message_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_chat_messages_reply_to_not_self",
        "chat_messages",
        "reply_to_message_id IS NULL OR reply_to_message_id <> id",
    )


def downgrade() -> None:
    op.drop_constraint("ck_chat_messages_reply_to_not_self", "chat_messages", type_="check")
    op.drop_constraint("fk_chat_messages_reply_to", "chat_messages", type_="foreignkey")
    op.drop_column("chat_messages", "reply_to_message_id")
