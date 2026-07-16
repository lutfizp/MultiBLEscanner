"""Add an auditable location-anchor timestamp to logical devices.

Revision ID: 0005
Revises: 0004
"""

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("logical_devices")}
    if "location_anchor_observed_at" in columns:
        return
    op.add_column(
        "logical_devices",
        sa.Column("location_anchor_observed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE logical_devices "
            "SET location_anchor_observed_at = last_seen_at "
            "WHERE location_anchor_observed_at IS NULL"
        )
    )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("logical_devices")}
    if "location_anchor_observed_at" in columns:
        op.drop_column("logical_devices", "location_anchor_observed_at")
