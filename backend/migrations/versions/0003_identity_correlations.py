"""Add auditable observed-identity correlations.

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "device_identity_correlations" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "device_identity_correlations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("predecessor_identity_id", sa.String(length=36), sa.ForeignKey("observed_identities.id"), nullable=False),
        sa.Column("successor_identity_id", sa.String(length=36), sa.ForeignKey("observed_identities.id"), nullable=False),
        sa.Column("predecessor_logical_device_id", sa.String(length=36), sa.ForeignKey("logical_devices.id"), nullable=False),
        sa.Column("successor_logical_device_id", sa.String(length=36), sa.ForeignKey("logical_devices.id"), nullable=False),
        sa.Column("method", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False, server_default="proposal"),
        sa.Column("time_difference_seconds", sa.Float()),
        sa.Column("rssi_difference_db", sa.Float()),
        sa.Column("assignment_cost", sa.Float()),
        sa.Column("alpha", sa.Float()),
        sa.Column("search_window_seconds", sa.Float()),
        sa.Column("evaluation_window_seconds", sa.Float()),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "predecessor_identity_id",
            "successor_identity_id",
            "method",
            name="uq_identity_correlation_pair_method",
        ),
    )
    op.create_index(
        "ix_identity_correlation_successor_status",
        "device_identity_correlations",
        ["successor_identity_id", "status"],
    )
    op.create_index(
        "ix_identity_correlation_logical_status",
        "device_identity_correlations",
        ["predecessor_logical_device_id", "status"],
    )


def downgrade() -> None:
    if "device_identity_correlations" not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index("ix_identity_correlation_logical_status", table_name="device_identity_correlations")
    op.drop_index("ix_identity_correlation_successor_status", table_name="device_identity_correlations")
    op.drop_table("device_identity_correlations")
