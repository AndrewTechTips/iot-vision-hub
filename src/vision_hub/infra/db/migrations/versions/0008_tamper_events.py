"""Tamper events: an event is motion or the camera being covered, blurred or moved.

Revision ID: 0008
Revises: 0007
Created: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every event recorded before this was motion.
    with op.batch_alter_table("motion_events") as batch:
        batch.add_column(sa.Column("kind", sa.String(20), nullable=False, server_default="motion"))


def downgrade() -> None:
    with op.batch_alter_table("motion_events") as batch:
        batch.drop_column("kind")
