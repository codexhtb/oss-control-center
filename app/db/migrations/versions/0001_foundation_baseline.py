"""Foundation baseline without domain tables.

Revision ID: 0001_foundation
Revises:
"""

from collections.abc import Sequence

revision: str = "0001_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No domain tables are needed for health endpoints."""


def downgrade() -> None:
    """No domain tables were created by this revision."""
