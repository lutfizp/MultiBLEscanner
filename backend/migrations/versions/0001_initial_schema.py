"""Create the deterministic initial database schema.

Revision ID: 0001_initial_schema
Revises:
"""

from alembic import op
import sqlalchemy as sa


revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monitored_locations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("building", sa.String(length=120)),
        sa.Column("floor", sa.String(length=80)),
        sa.Column("room", sa.String(length=120)),
        sa.Column("zone", sa.String(length=120)),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("indoor_x", sa.Float()),
        sa.Column("indoor_y", sa.Float()),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_table(
        "scanners",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("hardware_id", sa.String(length=160), nullable=False, unique=True),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("installation_name", sa.String(length=160)),
        sa.Column("location_id", sa.String(length=36), sa.ForeignKey("monitored_locations.id")),
        sa.Column("building", sa.String(length=120)),
        sa.Column("floor", sa.String(length=80)),
        sa.Column("room", sa.String(length=120)),
        sa.Column("zone", sa.String(length=120)),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("indoor_x", sa.Float()),
        sa.Column("indoor_y", sa.Float()),
        sa.Column("orientation_deg", sa.Float()),
        sa.Column("firmware_version", sa.String(length=80)),
        sa.Column("hardware_version", sa.String(length=80)),
        sa.Column("network_info", sa.JSON(), nullable=False),
        sa.Column("last_connection_at", sa.DateTime(timezone=True)),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("uptime_seconds", sa.Integer()),
        sa.Column("reset_reason", sa.String(length=120)),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("maintenance_notes", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_table(
        "scanner_configurations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scanner_id", sa.String(length=64), sa.ForeignKey("scanners.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("scan_interval_ms", sa.Integer(), nullable=False),
        sa.Column("upload_interval_seconds", sa.Integer(), nullable=False),
        sa.Column("batch_size", sa.Integer(), nullable=False),
        sa.Column("rssi_min", sa.Integer(), nullable=False),
        sa.Column("presence_missing_seconds", sa.Integer(), nullable=False),
        sa.Column("presence_offline_seconds", sa.Integer(), nullable=False),
        sa.Column("extra", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint("scanner_id", name="uq_scanner_configurations_scanner_id"),
    )
    op.create_table(
        "scanner_heartbeats",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("scanner_id", sa.String(length=64), sa.ForeignKey("scanners.id"), nullable=False),
        sa.Column("message_id", sa.String(length=120), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("scanner_time", sa.DateTime(timezone=True)),
        sa.Column("uptime_seconds", sa.Integer()),
        sa.Column("firmware_version", sa.String(length=80)),
        sa.Column("network_state", sa.JSON(), nullable=False),
        sa.Column("health", sa.JSON(), nullable=False),
        sa.Column("buffer_usage", sa.Integer(), nullable=False),
        sa.Column("pending_observations", sa.Integer(), nullable=False),
        sa.Column("dropped_observations", sa.Integer(), nullable=False),
        sa.Column("config_version", sa.Integer()),
        sa.Column("config_status", sa.String(length=80)),
        sa.UniqueConstraint("scanner_id", "message_id", name="uq_scanner_heartbeat_message"),
    )
    op.create_index(
        "ix_heartbeats_scanner_received",
        "scanner_heartbeats",
        ["scanner_id", "received_at"],
    )
    op.create_table(
        "observed_identities",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("address", sa.String(length=80)),
        sa.Column("address_type", sa.String(length=80)),
        sa.Column("advertised_name", sa.String(length=240)),
        sa.Column("local_name", sa.String(length=240)),
        sa.Column("service_uuids", sa.JSON(), nullable=False),
        sa.Column("service_data", sa.JSON(), nullable=False),
        sa.Column("manufacturer_data", sa.Text()),
        sa.Column("appearance", sa.String(length=80)),
        sa.Column("advertising_flags", sa.JSON(), nullable=False),
        sa.Column("raw_advertising_payload", sa.Text()),
        sa.Column("raw_scan_response_payload", sa.Text()),
        sa.Column("randomized_address", sa.Boolean(), nullable=False),
        sa.Column("fingerprint", sa.String(length=128), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_observed_identity_address",
        "observed_identities",
        ["address", "address_type"],
    )
    op.create_index(
        "ix_observed_identity_fingerprint",
        "observed_identities",
        ["fingerprint"],
    )
    op.create_table(
        "logical_devices",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("alias", sa.String(length=180)),
        sa.Column("primary_address", sa.String(length=80)),
        sa.Column("primary_address_type", sa.String(length=80)),
        sa.Column("display_name", sa.String(length=240)),
        sa.Column("vendor", sa.String(length=160)),
        sa.Column("category", sa.String(length=120)),
        sa.Column("status", sa.String(length=60), nullable=False),
        sa.Column("movement_status", sa.String(length=60), nullable=False),
        sa.Column("known", sa.Boolean(), nullable=False),
        sa.Column("ignored", sa.Boolean(), nullable=False),
        sa.Column("identity_confidence", sa.Float(), nullable=False),
        sa.Column("location_confidence", sa.Float(), nullable=False),
        sa.Column("movement_confidence", sa.Float(), nullable=False),
        sa.Column("current_scanner_id", sa.String(length=64), sa.ForeignKey("scanners.id")),
        sa.Column("current_zone", sa.String(length=120)),
        sa.Column("proximity_band", sa.String(length=40), nullable=False),
        sa.Column("estimated_distance_m", sa.Float()),
        sa.Column("smoothed_rssi", sa.Float()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("identity_signature", sa.JSON(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_logical_devices_status", "logical_devices", ["status"])
    op.create_index("ix_logical_devices_last_seen", "logical_devices", ["last_seen_at"])
    op.create_index(
        "ix_logical_devices_primary_address",
        "logical_devices",
        ["primary_address", "primary_address_type"],
    )
    op.create_table(
        "observations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("scanner_id", sa.String(length=64), sa.ForeignKey("scanners.id"), nullable=False),
        sa.Column("batch_id", sa.String(length=120), nullable=False),
        sa.Column("observation_id", sa.String(length=120), nullable=False),
        sa.Column(
            "observed_identity_id",
            sa.String(length=36),
            sa.ForeignKey("observed_identities.id"),
            nullable=False,
        ),
        sa.Column(
            "logical_device_id",
            sa.String(length=36),
            sa.ForeignKey("logical_devices.id"),
            nullable=False,
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scanner_time", sa.DateTime(timezone=True)),
        sa.Column("server_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rssi", sa.Integer(), nullable=False),
        sa.Column("tx_power", sa.Integer()),
        sa.Column("estimated_distance_m", sa.Float()),
        sa.Column("advertising_type", sa.String(length=80)),
        sa.Column("service_uuids", sa.JSON(), nullable=False),
        sa.Column("service_data", sa.JSON(), nullable=False),
        sa.Column("manufacturer_data", sa.Text()),
        sa.Column("appearance", sa.String(length=80)),
        sa.Column("advertising_flags", sa.JSON(), nullable=False),
        sa.Column("connectable", sa.Boolean()),
        sa.Column("raw_advertising_payload", sa.Text()),
        sa.Column("raw_scan_response_payload", sa.Text()),
        sa.Column("packet_length", sa.Integer()),
        sa.Column("firmware_version", sa.String(length=80)),
        sa.Column("scanner_uptime_seconds", sa.Integer()),
        sa.Column("processing_notes", sa.JSON(), nullable=False),
        sa.UniqueConstraint(
            "scanner_id",
            "batch_id",
            "observation_id",
            name="uq_observation_scanner_batch_item",
        ),
    )
    op.create_index("ix_observations_scanner_time", "observations", ["scanner_id", "observed_at"])
    op.create_index("ix_observations_device_time", "observations", ["logical_device_id", "observed_at"])
    op.create_index("ix_observations_identity_time", "observations", ["observed_identity_id", "observed_at"])
    op.create_table(
        "device_location_estimates",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "logical_device_id",
            sa.String(length=36),
            sa.ForeignKey("logical_devices.id"),
            nullable=False,
        ),
        sa.Column("scanner_id", sa.String(length=64), sa.ForeignKey("scanners.id"), nullable=False),
        sa.Column("estimated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("zone", sa.String(length=120)),
        sa.Column("proximity_band", sa.String(length=40)),
        sa.Column("estimated_distance_m", sa.Float()),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("method", sa.String(length=80), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_location_estimates_device_time",
        "device_location_estimates",
        ["logical_device_id", "estimated_at"],
    )
    op.create_table(
        "device_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=40), nullable=False),
        sa.Column("scanner_id", sa.String(length=64), sa.ForeignKey("scanners.id")),
        sa.Column("logical_device_id", sa.String(length=36), sa.ForeignKey("logical_devices.id")),
        sa.Column("observed_identity_id", sa.String(length=36), sa.ForeignKey("observed_identities.id")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("previous_state", sa.String(length=120)),
        sa.Column("new_state", sa.String(length=120)),
        sa.Column("previous_location", sa.String(length=180)),
        sa.Column("new_location", sa.String(length=180)),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=240), unique=True),
    )
    op.create_index("ix_device_events_occurred", "device_events", ["occurred_at"])
    op.create_index("ix_device_events_type_time", "device_events", ["event_type", "occurred_at"])
    op.create_table(
        "manual_device_correlation_decisions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column(
            "source_logical_device_id",
            sa.String(length=36),
            sa.ForeignKey("logical_devices.id"),
            nullable=False,
        ),
        sa.Column("target_logical_device_id", sa.String(length=36), sa.ForeignKey("logical_devices.id")),
        sa.Column("observed_identity_id", sa.String(length=36), sa.ForeignKey("observed_identities.id")),
        sa.Column("reason", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_table(
        "processing_errors",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("scanner_id", sa.String(length=64), sa.ForeignKey("scanners.id")),
        sa.Column("batch_id", sa.String(length=120)),
        sa.Column("observation_id", sa.String(length=120)),
        sa.Column("error_category", sa.String(length=120), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload_excerpt", sa.JSON()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
    )
    op.create_index("ix_processing_errors_created", "processing_errors", ["created_at"])


def downgrade() -> None:
    op.drop_table("processing_errors")
    op.drop_table("system_settings")
    op.drop_table("manual_device_correlation_decisions")
    op.drop_table("device_events")
    op.drop_table("device_location_estimates")
    op.drop_table("observations")
    op.drop_table("logical_devices")
    op.drop_table("observed_identities")
    op.drop_table("scanner_heartbeats")
    op.drop_table("scanner_configurations")
    op.drop_table("scanners")
    op.drop_table("monitored_locations")
