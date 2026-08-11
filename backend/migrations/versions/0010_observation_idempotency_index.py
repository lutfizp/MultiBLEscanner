"""Index the observation idempotency lookup.

Revision ID: 0010
Revises: 0009
"""

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


INDEX_NAME = "ix_observations_scanner_observation"


def upgrade() -> None:
    indexes = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("observations")
    }
    if INDEX_NAME not in indexes:
        op.create_index(
            INDEX_NAME,
            "observations",
            ["scanner_id", "observation_id"],
        )


def downgrade() -> None:
    indexes = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("observations")
    }
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name="observations")
