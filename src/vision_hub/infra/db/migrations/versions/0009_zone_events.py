"""Zone events: the zone a rule (someone lingering, a line crossed) fired in.

Revision ID: 0009
Revises: 0008
Created: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("motion_events") as batch:
        batch.add_column(sa.Column("zone", sa.String(40), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("motion_events") as batch:
        batch.drop_column("zone")
