"""Move weak-signal filtering out of radio admission.

Revision ID: 0009
Revises: 0008
"""

from alembic import op
import sqlalchemy as sa


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    configurations = sa.table(
        "scanner_configurations",
        sa.column("rssi_min", sa.Integer()),
    )
    op.execute(configurations.update().values(rssi_min=-110))


def downgrade() -> None:
    configurations = sa.table(
        "scanner_configurations",
        sa.column("rssi_min", sa.Integer()),
    )
    op.execute(configurations.update().values(rssi_min=-85))
