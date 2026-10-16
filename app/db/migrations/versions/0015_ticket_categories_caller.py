"""Add managed categories and an immutable caller number to tickets.

Revision ID: 0015_ticket_categories
Revises: 0014_ticket_deletion
"""

from collections.abc import Sequence
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0015_ticket_categories"
down_revision: str | None = "0014_ticket_deletion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INITIAL = (
    "Нет интернета",
    "Низкая скорость",
    "Нестабильный интернет",
    "IP-адрес",
    "Консультация по приставкам",
    "Консультация по тарифам",
    "Настройка оборудования",
    "Другое",
)


def upgrade() -> None:
    table = op.create_table(
        "ticket_categories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_other", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0 AND name = btrim(name) AND name !~ '[[:cntrl:]]'",
            name="ck_ticket_categories_name",
        ),
        sa.CheckConstraint("sort_order >= 0", name="ck_ticket_categories_order"),
        sa.CheckConstraint("version > 0", name="ck_ticket_categories_version"),
    )
    op.create_index(
        "uq_ticket_categories_name_lower",
        "ticket_categories",
        [sa.text("lower(name)")],
        unique=True,
    )
    op.create_index(
        "uq_ticket_categories_other",
        "ticket_categories",
        ["is_other"],
        unique=True,
        postgresql_where=sa.text("is_other"),
    )
    op.create_index(
        "ix_ticket_categories_active_order", "ticket_categories", ["is_active", "sort_order", "id"]
    )
    op.bulk_insert(
        table,
        [
            {
                "id": uuid5(NAMESPACE_URL, f"oss-control-center/category/{name}"),
                "name": name,
                "sort_order": index * 10,
                "is_active": True,
                "is_other": name == "Другое",
            }
            for index, name in enumerate(INITIAL, start=1)
        ],
    )
    op.add_column("tickets", sa.Column("category_id", UUID(as_uuid=True)))
    op.add_column("tickets", sa.Column("category_name", sa.String(160)))
    op.add_column("tickets", sa.Column("caller_phone", sa.String(50)))
    op.create_foreign_key(
        "fk_tickets_category",
        "tickets",
        "ticket_categories",
        ["category_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_tickets_category_snapshot",
        "tickets",
        "(category_id IS NULL AND category_name IS NULL) OR "
        "(category_id IS NOT NULL AND category_name IS NOT NULL "
        "AND length(btrim(category_name)) > 0)",
    )
    op.create_check_constraint(
        "ck_tickets_caller_phone",
        "tickets",
        "caller_phone IS NULL OR (length(btrim(caller_phone)) > 0 "
        "AND caller_phone = btrim(caller_phone) AND caller_phone !~ '[[:cntrl:]]')",
    )
    op.create_index("ix_tickets_category_created", "tickets", ["category_id", "created_at"])
    op.execute("""
        CREATE FUNCTION ticket_origin_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.category_id IS DISTINCT FROM NEW.category_id
             OR OLD.category_name IS DISTINCT FROM NEW.category_name
             OR OLD.caller_phone IS DISTINCT FROM NEW.caller_phone THEN
            RAISE EXCEPTION USING MESSAGE = 'ticket origin fields are immutable', ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER immutable_ticket_origin BEFORE UPDATE ON tickets
        FOR EACH ROW EXECUTE FUNCTION ticket_origin_immutable()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER immutable_ticket_origin ON tickets")
    op.execute("DROP FUNCTION ticket_origin_immutable()")
    op.drop_index("ix_tickets_category_created", table_name="tickets")
    op.drop_constraint("ck_tickets_caller_phone", "tickets", type_="check")
    op.drop_constraint("ck_tickets_category_snapshot", "tickets", type_="check")
    op.drop_constraint("fk_tickets_category", "tickets", type_="foreignkey")
    op.drop_column("tickets", "caller_phone")
    op.drop_column("tickets", "category_name")
    op.drop_column("tickets", "category_id")
    op.drop_index("ix_ticket_categories_active_order", table_name="ticket_categories")
    op.drop_index("uq_ticket_categories_other", table_name="ticket_categories")
    op.drop_index("uq_ticket_categories_name_lower", table_name="ticket_categories")
    op.drop_table("ticket_categories")
