"""Ticket-bound internal tasks, conversation, notification inbox and grants.

Revision ID: 0043_internal_tasks
Revises: 0042_reports_permission
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0043_internal_tasks"
down_revision: str | None = "0042_reports_permission"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PREVIOUS = (
    "'users.manage','users.read','tickets.create','tickets.read.all','tickets.read.own',"
    "'tickets.edit.all','tickets.edit.own','tickets.comment','tickets.assign','tickets.start',"
    "'tickets.diagnose','tickets.resolve','tickets.close','tickets.callback',"
    "'subscribers.read','subscribers.finance.read','diagnostics.read','diagnostics.run',"
    "'audit.read','tickets.field_work','tickets.delete.own','tickets.delete.all',"
    "'tickets.restore','tickets.purge','chat.messages.delete','settings.manage','reports.read'"
)


def upgrade() -> None:
    op.drop_constraint("ck_app_user_permissions_known", "app_user_permissions", type_="check")
    op.create_check_constraint(
        "ck_app_user_permissions_known",
        "app_user_permissions",
        f"permission IN ({PREVIOUS},'tasks.create','tasks.execute')",
    )
    op.execute("""
        INSERT INTO app_user_permissions (user_id, permission)
        SELECT u.id, p.permission FROM app_users u
        CROSS JOIN (VALUES ('tasks.create'), ('tasks.execute')) AS p(permission)
        WHERE u.role IN ('engineer','admin') AND u.deleted_at IS NULL
          AND u.permissions_customized = TRUE
        ON CONFLICT (user_id, permission) DO NOTHING
    """)
    op.create_table(
        "internal_tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "ticket_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tickets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "creator_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app_users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "assignee_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app_users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("client_request_id", UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False, server_default="OPEN"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "creator_id", "client_request_id", name="uq_internal_tasks_creator_request"
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','COMPLETED','NO_ANSWER')", name="ck_internal_tasks_status"
        ),
        sa.CheckConstraint("version > 0", name="ck_internal_tasks_version"),
        sa.CheckConstraint(
            "length(btrim(title)) BETWEEN 1 AND 160", name="ck_internal_tasks_title"
        ),
        sa.CheckConstraint(
            "length(btrim(description)) BETWEEN 1 AND 4000", name="ck_internal_tasks_description"
        ),
    )
    op.create_index(
        "ix_internal_tasks_ticket_created", "internal_tasks", ["ticket_id", "created_at", "id"]
    )
    op.create_index(
        "ix_internal_tasks_assignee_status", "internal_tasks", ["assignee_id", "status", "due_at"]
    )
    op.create_index(
        "ix_internal_tasks_creator_status", "internal_tasks", ["creator_id", "status", "created_at"]
    )
    op.create_table(
        "internal_task_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "task_id",
            UUID(as_uuid=True),
            sa.ForeignKey("internal_tasks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "actor_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app_users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("client_request_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "task_id", "actor_id", "client_request_id", name="uq_internal_task_events_request"
        ),
        sa.CheckConstraint(
            "kind IN ('CREATED','MESSAGE','COMPLETED','NO_ANSWER','REOPENED')",
            name="ck_internal_task_events_kind",
        ),
        sa.CheckConstraint(
            "body IS NULL OR length(btrim(body)) BETWEEN 1 AND 4000",
            name="ck_internal_task_events_body",
        ),
    )
    op.create_index(
        "ix_internal_task_events_task_created",
        "internal_task_events",
        ["task_id", "created_at", "id"],
    )
    op.create_table(
        "internal_task_notifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "task_id",
            UUID(as_uuid=True),
            sa.ForeignKey("internal_tasks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("internal_task_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "recipient_id",
            UUID(as_uuid=True),
            sa.ForeignKey("app_users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("read_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "event_id", "recipient_id", name="uq_internal_task_notification_event_recipient"
        ),
    )
    op.create_index(
        "ix_internal_task_notifications_recipient_created",
        "internal_task_notifications",
        ["recipient_id", "created_at", "id"],
    )
    op.create_index(
        "ix_internal_task_notifications_unread",
        "internal_task_notifications",
        ["recipient_id", "created_at"],
        postgresql_where=sa.text("read_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("internal_task_notifications")
    op.drop_table("internal_task_events")
    op.drop_table("internal_tasks")
    op.execute(
        "DELETE FROM app_user_permissions WHERE permission IN ('tasks.create','tasks.execute')"
    )
    op.drop_constraint("ck_app_user_permissions_known", "app_user_permissions", type_="check")
    op.create_check_constraint(
        "ck_app_user_permissions_known", "app_user_permissions", f"permission IN ({PREVIOUS})"
    )
