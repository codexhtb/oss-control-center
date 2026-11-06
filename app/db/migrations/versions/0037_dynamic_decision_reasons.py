"""Allow catalog-backed decision reasons beyond the original nine codes.

Revision ID: 0037_dynamic_decision_reasons
Revises: 0036_service_desk_settings
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0037_dynamic_decision_reasons"
down_revision: str | None = "0036_service_desk_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_CODES = (
    "CABLE_BREAK", "CUSTOMER_EQUIPMENT", "ONU_POWER", "NETWORK_FAULT",
    "CONFIGURATION_IP", "CONNECTION_SETUP", "CONSULTATION", "OTHER",
    "NOT_ESTABLISHED",
)


def upgrade() -> None:
    # The 0036 FK to decision_reason_catalog remains the database authority for
    # valid codes. The old CHECK was a second, fixed list that rejected new rows.
    op.drop_constraint("ck_ticket_decisions_reason", "ticket_decisions", type_="check")
    op.create_index(
        "uq_decision_reason_name_ci", "decision_reason_catalog", [sa.text("lower(name)")],
        unique=True,
    )


def downgrade() -> None:
    # Reinstating the fixed CHECK while custom codes exist would discard the
    # ability to write their decisions. Require an explicit data review instead.
    codes = ", ".join(f"'{code}'" for code in LEGACY_CODES)
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM decision_reason_catalog "
        f"WHERE code NOT IN ({codes})) THEN "
        "RAISE EXCEPTION 'custom decision reasons prevent downgrade'; "
        "END IF; END $$"
    )
    op.drop_index("uq_decision_reason_name_ci", table_name="decision_reason_catalog")
    op.create_check_constraint(
        "ck_ticket_decisions_reason", "ticket_decisions",
        f"reason IS NULL OR reason IN ({codes})",
    )
