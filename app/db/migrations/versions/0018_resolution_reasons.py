"""Add structured resolution reasons and make legacy action text optional.

Revision ID: 0018_resolution_reasons
Revises: 0017_ticket_callbacks
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_resolution_reasons"
down_revision: str | None = "0017_ticket_callbacks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ticket_decisions", sa.Column("reason", sa.String(32)))
    op.create_check_constraint(
        "ck_ticket_decisions_reason",
        "ticket_decisions",
        "reason IS NULL OR reason IN ("
        "'CABLE_BREAK','CUSTOMER_EQUIPMENT','ONU_POWER','NETWORK_FAULT',"
        "'CONFIGURATION_IP','CONNECTION_SETUP','CONSULTATION','OTHER','NOT_ESTABLISHED')",
    )
    op.drop_constraint("ck_ticket_decisions_actions", "ticket_decisions", type_="check")
    op.alter_column("ticket_decisions", "performed_actions", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM ticket_decisions "
        "WHERE reason IS NOT NULL OR performed_actions IS NULL)"
    )):
        raise RuntimeError("Resolution reasons or decisions without legacy actions would be lost")
    op.alter_column(
        "ticket_decisions", "performed_actions", existing_type=sa.Text(), nullable=False
    )
    op.create_check_constraint(
        "ck_ticket_decisions_actions", "ticket_decisions", "length(performed_actions) > 0"
    )
    op.drop_constraint("ck_ticket_decisions_reason", "ticket_decisions", type_="check")
    op.drop_column("ticket_decisions", "reason")
