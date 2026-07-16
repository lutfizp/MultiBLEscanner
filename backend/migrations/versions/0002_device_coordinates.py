"""Add latitude and longitude to logical_devices."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade():
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("logical_devices")}
    if "latitude" not in existing:
        op.add_column("logical_devices", sa.Column("latitude", sa.Float(), nullable=True))
    if "longitude" not in existing:
        op.add_column("logical_devices", sa.Column("longitude", sa.Float(), nullable=True))


def downgrade():
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("logical_devices")}
    if "longitude" in existing:
        op.drop_column("logical_devices", "longitude")
    if "latitude" in existing:
        op.drop_column("logical_devices", "latitude")
