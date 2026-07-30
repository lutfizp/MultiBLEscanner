"""Add focused BLE tracking sessions and scanner-position evidence.

Revision ID: 0006
Revises: 0005
"""

from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "device_tracking_sessions" in inspector.get_table_names():
        return

    op.create_table(
        "device_tracking_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("logical_device_id", sa.String(length=36), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_lease_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_reason", sa.String(length=120), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["logical_device_id"], ["logical_devices.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tracking_sessions_device_started",
        "device_tracking_sessions",
        ["logical_device_id", "started_at"],
    )
    op.create_index(
        "ix_tracking_sessions_state_expiry",
        "device_tracking_sessions",
        ["state", "expires_at"],
    )

    op.create_table(
        "device_tracking_scanners",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("scanner_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("target_identities", sa.JSON(), nullable=False),
        sa.Column("fixed_latitude", sa.Float(), nullable=True),
        sa.Column("fixed_longitude", sa.Float(), nullable=True),
        sa.Column("armed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sample_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_boot_id", sa.String(length=160), nullable=True),
        sa.Column("last_sequence", sa.Integer(), nullable=True),
        sa.Column("smoothed_rssi", sa.Float(), nullable=True),
        sa.Column("dropped_samples", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["scanner_id"], ["scanners.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["device_tracking_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "scanner_id", name="uq_tracking_scanner_session"),
    )
    op.create_index(
        "ix_tracking_scanners_scanner_state",
        "device_tracking_scanners",
        ["scanner_id", "state"],
    )

    op.create_table(
        "device_tracking_samples",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("assignment_id", sa.String(length=36), nullable=False),
        sa.Column("scanner_id", sa.String(length=64), nullable=False),
        sa.Column("observed_identity_id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=120), nullable=False),
        sa.Column("sample_id", sa.String(length=120), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("server_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("boot_id", sa.String(length=160), nullable=False),
        sa.Column("monotonic_ms", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("address", sa.String(length=80), nullable=False),
        sa.Column("address_type", sa.String(length=80), nullable=False),
        sa.Column("rssi", sa.Integer(), nullable=False),
        sa.Column("smoothed_rssi", sa.Float(), nullable=False),
        sa.Column("signal_level", sa.Float(), nullable=False),
        sa.Column("delayed", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["assignment_id"], ["device_tracking_scanners.id"]),
        sa.ForeignKeyConstraint(["observed_identity_id"], ["observed_identities.id"]),
        sa.ForeignKeyConstraint(["scanner_id"], ["scanners.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["device_tracking_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scanner_id", "sample_id", name="uq_tracking_sample_scanner_item"),
    )
    op.create_index(
        "ix_tracking_samples_assignment_sequence",
        "device_tracking_samples",
        ["assignment_id", "sequence"],
    )
    op.create_index(
        "ix_tracking_samples_session_time",
        "device_tracking_samples",
        ["session_id", "observed_at"],
    )

    op.create_table(
        "device_tracking_positions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("scanner_id", sa.String(length=64), nullable=False),
        sa.Column("position_id", sa.String(length=120), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("server_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("accuracy_m", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["scanner_id"], ["scanners.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["device_tracking_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "position_id", name="uq_tracking_position_session_item"),
    )
    op.create_index(
        "ix_tracking_positions_session_time",
        "device_tracking_positions",
        ["session_id", "observed_at"],
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "device_tracking_positions" in tables:
        op.drop_index("ix_tracking_positions_session_time", table_name="device_tracking_positions")
        op.drop_table("device_tracking_positions")
    if "device_tracking_samples" in tables:
        op.drop_index("ix_tracking_samples_session_time", table_name="device_tracking_samples")
        op.drop_index("ix_tracking_samples_assignment_sequence", table_name="device_tracking_samples")
        op.drop_table("device_tracking_samples")
    if "device_tracking_scanners" in tables:
        op.drop_index("ix_tracking_scanners_scanner_state", table_name="device_tracking_scanners")
        op.drop_table("device_tracking_scanners")
    if "device_tracking_sessions" in tables:
        op.drop_index("ix_tracking_sessions_state_expiry", table_name="device_tracking_sessions")
        op.drop_index("ix_tracking_sessions_device_started", table_name="device_tracking_sessions")
        op.drop_table("device_tracking_sessions")
