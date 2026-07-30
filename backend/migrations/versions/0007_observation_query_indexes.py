"""Add indexes used by dashboard observation summaries.

Revision ID: 0007
Revises: 0006
"""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    indexes = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("observations")
    }
    if "ix_observations_received" not in indexes:
        op.create_index(
            "ix_observations_received",
            "observations",
            ["server_received_at"],
        )
    if "ix_observations_device_identity" not in indexes:
        op.create_index(
            "ix_observations_device_identity",
            "observations",
            ["logical_device_id", "observed_identity_id"],
        )


def downgrade() -> None:
    indexes = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("observations")
    }
    if "ix_observations_device_identity" in indexes:
        op.drop_index(
            "ix_observations_device_identity",
            table_name="observations",
        )
    if "ix_observations_received" in indexes:
        op.drop_index(
            "ix_observations_received",
            table_name="observations",
        )
