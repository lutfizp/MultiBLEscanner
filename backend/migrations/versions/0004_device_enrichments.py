"""Add direct device-information enrichment history.

Revision ID: 0004
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "device_enrichments" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "device_enrichments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("logical_device_id", sa.String(length=36), sa.ForeignKey("logical_devices.id"), nullable=False),
        sa.Column("observed_identity_id", sa.String(length=36), sa.ForeignKey("observed_identities.id"), nullable=False),
        sa.Column("scanner_id", sa.String(length=64), sa.ForeignKey("scanners.id"), nullable=False),
        sa.Column("source_observation_id", sa.String(length=120), nullable=False),
        sa.Column("enriched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transport", sa.String(length=40), nullable=False, server_default="ble_gatt"),
        sa.Column("status", sa.String(length=60), nullable=False),
        sa.Column("device_name", sa.String(length=240)),
        sa.Column("manufacturer_name", sa.String(length=240)),
        sa.Column("model_number", sa.String(length=240)),
        sa.Column("serial_number", sa.String(length=240)),
        sa.Column("firmware_revision", sa.String(length=240)),
        sa.Column("hardware_revision", sa.String(length=240)),
        sa.Column("software_revision", sa.String(length=240)),
        sa.Column("system_id", sa.String(length=128)),
        sa.Column("pnp_id", sa.String(length=128)),
        sa.Column("discovered_services", sa.JSON(), nullable=False),
        sa.Column("characteristic_values", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=120)),
        sa.Column("attempt_duration_ms", sa.Integer()),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "scanner_id",
            "source_observation_id",
            "transport",
            name="uq_device_enrichment_source_transport",
        ),
    )
    op.create_index("ix_device_enrichment_device_time", "device_enrichments", ["logical_device_id", "enriched_at"])
    op.create_index("ix_device_enrichment_identity_time", "device_enrichments", ["observed_identity_id", "enriched_at"])


def downgrade() -> None:
    if "device_enrichments" not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index("ix_device_enrichment_identity_time", table_name="device_enrichments")
    op.drop_index("ix_device_enrichment_device_time", table_name="device_enrichments")
    op.drop_table("device_enrichments")
