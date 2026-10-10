"""Event moments: when the person was found, the snapshot taken and the clip started.

Revision ID: 0007
Revises: 0006
Created: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Unknown (NULL) for events recorded before: their clips simply have no markers.
    with op.batch_alter_table("motion_events") as batch:
        batch.add_column(sa.Column("person_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("clip_started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("motion_events") as batch:
        batch.drop_column("clip_started_at")
        batch.drop_column("snapshot_at")
        batch.drop_column("person_at")
