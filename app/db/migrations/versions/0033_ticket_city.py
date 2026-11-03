"""Store the contact city on each ticket.

Revision ID: 0033_ticket_city
Revises: 0032_live_user_login_unique
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033_ticket_city"
down_revision: str | None = "0032_live_user_login_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("city", sa.String(length=160), nullable=True))
    op.create_check_constraint(
        "ck_tickets_city",
        "tickets",
        "city IS NULL OR (length(btrim(city)) > 0 AND city = btrim(city) "
        "AND city !~ '[[:cntrl:]]')",
    )
    op.execute("""
        CREATE OR REPLACE FUNCTION ticket_origin_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.category_id IS DISTINCT FROM NEW.category_id
             OR OLD.category_name IS DISTINCT FROM NEW.category_name
             OR OLD.caller_phone IS DISTINCT FROM NEW.caller_phone
             OR OLD.city IS DISTINCT FROM NEW.city THEN
            RAISE EXCEPTION USING MESSAGE = 'ticket origin fields are immutable', ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$
    """)


def downgrade() -> None:
    op.execute("""
        CREATE OR REPLACE FUNCTION ticket_origin_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.category_id IS DISTINCT FROM NEW.category_id
             OR OLD.category_name IS DISTINCT FROM NEW.category_name
             OR OLD.caller_phone IS DISTINCT FROM NEW.caller_phone THEN
            RAISE EXCEPTION USING MESSAGE = 'ticket origin fields are immutable', ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$
    """)
    op.drop_constraint("ck_tickets_city", "tickets", type_="check")
    op.drop_column("tickets", "city")
