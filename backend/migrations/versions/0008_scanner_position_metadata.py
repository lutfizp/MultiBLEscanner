"""Add current scanner position provenance.

Revision ID: 0008
Revises: 0007
"""

from alembic import op
import sqlalchemy as sa


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("scanners")
    }
    if "location_source" not in columns:
        op.add_column("scanners", sa.Column("location_source", sa.String(length=60)))
    if "location_observed_at" not in columns:
        op.add_column("scanners", sa.Column("location_observed_at", sa.DateTime(timezone=True)))
    if "location_accuracy_m" not in columns:
        op.add_column("scanners", sa.Column("location_accuracy_m", sa.Float()))


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("scanners")
    }
    if "location_accuracy_m" in columns:
        op.drop_column("scanners", "location_accuracy_m")
    if "location_observed_at" in columns:
        op.drop_column("scanners", "location_observed_at")
    if "location_source" in columns:
        op.drop_column("scanners", "location_source")
