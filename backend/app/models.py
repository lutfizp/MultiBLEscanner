import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from .database import Base


def uuid_str() -> str:
    return str(uuid.uuid4())


class Scanner(Base):
    __tablename__ = "scanners"

    id = Column(String(64), primary_key=True)
    display_name = Column(String(160), nullable=False)
    hardware_id = Column(String(160), nullable=False, unique=True)
    token_hash = Column(String(128), nullable=False)
    installation_name = Column(String(160))
    building = Column(String(120))
    floor = Column(String(80))
    room = Column(String(120))
    zone = Column(String(120))
    latitude = Column(Float)
    longitude = Column(Float)
    location_source = Column(String(60))
    location_observed_at = Column(DateTime(timezone=True))
    location_accuracy_m = Column(Float)
    indoor_x = Column(Float)
    indoor_y = Column(Float)
    orientation_deg = Column(Float)
    firmware_version = Column(String(80))
    hardware_version = Column(String(80))
    network_info = Column(JSON, nullable=False, default=dict)
    last_connection_at = Column(DateTime(timezone=True))
    last_heartbeat_at = Column(DateTime(timezone=True))
    last_seen_at = Column(DateTime(timezone=True))
    status = Column(String(40), nullable=False, default="registered")
    uptime_seconds = Column(Integer)
    reset_reason = Column(String(120))
    config_version = Column(Integer, nullable=False, default=1)
    enabled = Column(Boolean, nullable=False, default=True)
    maintenance_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class ScannerConfiguration(Base):
    __tablename__ = "scanner_configurations"

    id = Column(Integer, primary_key=True)
    scanner_id = Column(String(64), ForeignKey("scanners.id"), nullable=False, unique=True)
    version = Column(Integer, nullable=False, default=1)
    scan_interval_ms = Column(Integer, nullable=False, default=5000)
    upload_interval_seconds = Column(Integer, nullable=False, default=5)
    batch_size = Column(Integer, nullable=False, default=40)
    rssi_min = Column(Integer, nullable=False, default=-110)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    scanner = relationship("Scanner")


class ScannerHeartbeat(Base):
    __tablename__ = "scanner_heartbeats"

    id = Column(String(36), primary_key=True, default=uuid_str)
    scanner_id = Column(String(64), ForeignKey("scanners.id"), nullable=False)
    message_id = Column(String(120), nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    scanner_time = Column(DateTime(timezone=True))
    uptime_seconds = Column(Integer)
    firmware_version = Column(String(80))
    network_state = Column(JSON, nullable=False, default=dict)
    health = Column(JSON, nullable=False, default=dict)
    buffer_usage = Column(Integer, nullable=False, default=0)
    pending_observations = Column(Integer, nullable=False, default=0)
    dropped_observations = Column(Integer, nullable=False, default=0)
    config_version = Column(Integer)
    config_status = Column(String(80))

    __table_args__ = (
        UniqueConstraint("scanner_id", "message_id", name="uq_scanner_heartbeat_message"),
        Index("ix_heartbeats_scanner_received", "scanner_id", "received_at"),
    )


class ObservedIdentity(Base):
    __tablename__ = "observed_identities"

    id = Column(String(36), primary_key=True, default=uuid_str)
    address = Column(String(80))
    address_type = Column(String(80))
    advertised_name = Column(String(240))
    local_name = Column(String(240))
    service_uuids = Column(JSON, nullable=False, default=list)
    service_data = Column(JSON, nullable=False, default=dict)
    manufacturer_data = Column(Text)
    appearance = Column(String(80))
    advertising_flags = Column(JSON, nullable=False, default=dict)
    raw_advertising_payload = Column(Text)
    raw_scan_response_payload = Column(Text)
    randomized_address = Column(Boolean, nullable=False, default=False)
    first_seen_at = Column(DateTime(timezone=True), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=False)
    observation_count = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_observed_identity_address", "address", "address_type"),
    )


class LogicalDevice(Base):
    __tablename__ = "logical_devices"

    id = Column(String(36), primary_key=True, default=uuid_str)
    alias = Column(String(180))
    primary_address = Column(String(80))
    primary_address_type = Column(String(80))
    display_name = Column(String(240))
    vendor = Column(String(160))
    category = Column(String(120))
    status = Column(String(60), nullable=False, default="newly_detected")
    movement_status = Column(String(60), nullable=False, default="stationary")
    known = Column(Boolean, nullable=False, default=False)
    ignored = Column(Boolean, nullable=False, default=False)
    identity_confidence = Column(Float, nullable=False, default=0.5)
    location_confidence = Column(Float, nullable=False, default=0.0)
    movement_confidence = Column(Float, nullable=False, default=0.0)
    current_scanner_id = Column(String(64), ForeignKey("scanners.id"))
    current_zone = Column(String(120))
    proximity_band = Column(String(40), nullable=False, default="unknown")
    estimated_distance_m = Column(Float)
    smoothed_rssi = Column(Float)
    latitude = Column(Float)
    longitude = Column(Float)
    location_anchor_observed_at = Column(DateTime(timezone=True))
    first_seen_at = Column(DateTime(timezone=True), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=False)
    observation_count = Column(Integer, nullable=False, default=0)
    notes = Column(Text)
    tags = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    current_scanner = relationship("Scanner")

    __table_args__ = (
        Index("ix_logical_devices_status", "status"),
        Index("ix_logical_devices_last_seen", "last_seen_at"),
        Index("ix_logical_devices_primary_address", "primary_address", "primary_address_type"),
    )


class Observation(Base):
    __tablename__ = "observations"

    id = Column(String(36), primary_key=True, default=uuid_str)
    scanner_id = Column(String(64), ForeignKey("scanners.id"), nullable=False)
    batch_id = Column(String(120), nullable=False)
    observation_id = Column(String(120), nullable=False)
    observed_identity_id = Column(String(36), ForeignKey("observed_identities.id"), nullable=False)
    logical_device_id = Column(String(36), ForeignKey("logical_devices.id"), nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    scanner_time = Column(DateTime(timezone=True))
    server_received_at = Column(DateTime(timezone=True), nullable=False)
    processed_at = Column(DateTime(timezone=True), nullable=False)
    rssi = Column(Integer, nullable=False)
    tx_power = Column(Integer)
    estimated_distance_m = Column(Float)
    advertising_type = Column(String(80))
    service_uuids = Column(JSON, nullable=False, default=list)
    service_data = Column(JSON, nullable=False, default=dict)
    manufacturer_data = Column(Text)
    appearance = Column(String(80))
    advertising_flags = Column(JSON, nullable=False, default=dict)
    connectable = Column(Boolean)
    raw_advertising_payload = Column(Text)
    raw_scan_response_payload = Column(Text)
    packet_length = Column(Integer)
    firmware_version = Column(String(80))
    scanner_uptime_seconds = Column(Integer)
    processing_notes = Column(JSON, nullable=False, default=dict)

    scanner = relationship("Scanner")
    observed_identity = relationship("ObservedIdentity")
    logical_device = relationship("LogicalDevice")

    __table_args__ = (
        UniqueConstraint("scanner_id", "batch_id", "observation_id", name="uq_observation_scanner_batch_item"),
        Index("ix_observations_received", "server_received_at"),
        Index("ix_observations_scanner_observation", "scanner_id", "observation_id"),
        Index("ix_observations_device_identity", "logical_device_id", "observed_identity_id"),
        Index("ix_observations_scanner_time", "scanner_id", "observed_at"),
        Index("ix_observations_device_time", "logical_device_id", "observed_at"),
        Index("ix_observations_identity_time", "observed_identity_id", "observed_at"),
    )


class DeviceTrackingSession(Base):
    __tablename__ = "device_tracking_sessions"

    id = Column(String(36), primary_key=True, default=uuid_str)
    logical_device_id = Column(String(36), ForeignKey("logical_devices.id"), nullable=False)
    mode = Column(String(20), nullable=False, default="fixed")
    state = Column(String(40), nullable=False, default="arming")
    started_at = Column(DateTime(timezone=True), nullable=False)
    last_lease_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    ended_at = Column(DateTime(timezone=True))
    stop_reason = Column(String(120))
    summary = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    logical_device = relationship("LogicalDevice")

    __table_args__ = (
        Index("ix_tracking_sessions_device_started", "logical_device_id", "started_at"),
        Index("ix_tracking_sessions_state_expiry", "state", "expires_at"),
    )


class DeviceTrackingScanner(Base):
    __tablename__ = "device_tracking_scanners"

    id = Column(String(36), primary_key=True, default=uuid_str)
    session_id = Column(String(36), ForeignKey("device_tracking_sessions.id"), nullable=False)
    scanner_id = Column(String(64), ForeignKey("scanners.id"), nullable=False)
    state = Column(String(40), nullable=False, default="arming")
    target_identities = Column(JSON, nullable=False, default=list)
    fixed_latitude = Column(Float)
    fixed_longitude = Column(Float)
    armed_at = Column(DateTime(timezone=True))
    last_sample_at = Column(DateTime(timezone=True))
    last_boot_id = Column(String(160))
    last_sequence = Column(Integer)
    smoothed_rssi = Column(Float)
    dropped_samples = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    session = relationship("DeviceTrackingSession")
    scanner = relationship("Scanner")

    __table_args__ = (
        UniqueConstraint("session_id", "scanner_id", name="uq_tracking_scanner_session"),
        Index("ix_tracking_scanners_scanner_state", "scanner_id", "state"),
    )


class DeviceTrackingSample(Base):
    __tablename__ = "device_tracking_samples"

    id = Column(String(36), primary_key=True, default=uuid_str)
    session_id = Column(String(36), ForeignKey("device_tracking_sessions.id"), nullable=False)
    assignment_id = Column(String(36), ForeignKey("device_tracking_scanners.id"), nullable=False)
    scanner_id = Column(String(64), ForeignKey("scanners.id"), nullable=False)
    observed_identity_id = Column(String(36), ForeignKey("observed_identities.id"), nullable=False)
    batch_id = Column(String(120), nullable=False)
    sample_id = Column(String(120), nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    server_received_at = Column(DateTime(timezone=True), nullable=False)
    boot_id = Column(String(160), nullable=False)
    monotonic_ms = Column(Integer, nullable=False)
    sequence = Column(Integer, nullable=False)
    address = Column(String(80), nullable=False)
    address_type = Column(String(80), nullable=False)
    rssi = Column(Integer, nullable=False)
    smoothed_rssi = Column(Float, nullable=False)
    signal_level = Column(Float, nullable=False)
    delayed = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("scanner_id", "sample_id", name="uq_tracking_sample_scanner_item"),
        Index("ix_tracking_samples_session_time", "session_id", "observed_at"),
        Index("ix_tracking_samples_assignment_sequence", "assignment_id", "sequence"),
    )


class DeviceTrackingPosition(Base):
    __tablename__ = "device_tracking_positions"

    id = Column(String(36), primary_key=True, default=uuid_str)
    session_id = Column(String(36), ForeignKey("device_tracking_sessions.id"), nullable=False)
    scanner_id = Column(String(64), ForeignKey("scanners.id"), nullable=False)
    position_id = Column(String(120), nullable=False)
    observed_at = Column(DateTime(timezone=True), nullable=False)
    server_received_at = Column(DateTime(timezone=True), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    accuracy_m = Column(Float, nullable=False)

    __table_args__ = (
        UniqueConstraint("session_id", "position_id", name="uq_tracking_position_session_item"),
        Index("ix_tracking_positions_session_time", "session_id", "observed_at"),
    )


class DeviceEnrichment(Base):
    __tablename__ = "device_enrichments"

    id = Column(String(36), primary_key=True, default=uuid_str)
    logical_device_id = Column(String(36), ForeignKey("logical_devices.id"), nullable=False)
    observed_identity_id = Column(String(36), ForeignKey("observed_identities.id"), nullable=False)
    scanner_id = Column(String(64), ForeignKey("scanners.id"), nullable=False)
    source_observation_id = Column(String(120), nullable=False)
    enriched_at = Column(DateTime(timezone=True), nullable=False)
    transport = Column(String(40), nullable=False, default="ble_gatt")
    status = Column(String(60), nullable=False)
    device_name = Column(String(240))
    manufacturer_name = Column(String(240))
    model_number = Column(String(240))
    serial_number = Column(String(240))
    firmware_revision = Column(String(240))
    hardware_revision = Column(String(240))
    software_revision = Column(String(240))
    system_id = Column(String(128))
    pnp_id = Column(String(128))
    discovered_services = Column(JSON, nullable=False, default=list)
    characteristic_values = Column(JSON, nullable=False, default=dict)
    error_code = Column(String(120))
    attempt_duration_ms = Column(Integer)
    details = Column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint(
            "scanner_id",
            "source_observation_id",
            "transport",
            name="uq_device_enrichment_source_transport",
        ),
        Index("ix_device_enrichment_device_time", "logical_device_id", "enriched_at"),
        Index("ix_device_enrichment_identity_time", "observed_identity_id", "enriched_at"),
    )


class DeviceLocationEstimate(Base):
    __tablename__ = "device_location_estimates"

    id = Column(String(36), primary_key=True, default=uuid_str)
    logical_device_id = Column(String(36), ForeignKey("logical_devices.id"), nullable=False)
    scanner_id = Column(String(64), ForeignKey("scanners.id"), nullable=False)
    estimated_at = Column(DateTime(timezone=True), nullable=False)
    zone = Column(String(120))
    proximity_band = Column(String(40))
    estimated_distance_m = Column(Float)
    confidence = Column(Float, nullable=False, default=0.0)
    method = Column(String(80), nullable=False, default="single_scanner_rssi_band")
    details = Column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_location_estimates_device_time", "logical_device_id", "estimated_at"),
    )


class DeviceEvent(Base):
    __tablename__ = "device_events"

    id = Column(String(36), primary_key=True, default=uuid_str)
    event_type = Column(String(80), nullable=False)
    severity = Column(String(40), nullable=False, default="info")
    scanner_id = Column(String(64), ForeignKey("scanners.id"))
    logical_device_id = Column(String(36), ForeignKey("logical_devices.id"))
    observed_identity_id = Column(String(36), ForeignKey("observed_identities.id"))
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    received_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    previous_state = Column(String(120))
    new_state = Column(String(120))
    previous_location = Column(String(180))
    new_location = Column(String(180))
    confidence = Column(Float, nullable=False, default=0.0)
    reason = Column(Text)
    details = Column(JSON, nullable=False, default=dict)
    dedupe_key = Column(String(240), unique=True)

    scanner = relationship("Scanner")
    logical_device = relationship("LogicalDevice")
    observed_identity = relationship("ObservedIdentity")

    __table_args__ = (
        Index("ix_device_events_occurred", "occurred_at"),
        Index("ix_device_events_type_time", "event_type", "occurred_at"),
    )


class ManualDeviceCorrelationDecision(Base):
    __tablename__ = "manual_device_correlation_decisions"

    id = Column(String(36), primary_key=True, default=uuid_str)
    action = Column(String(40), nullable=False)
    source_logical_device_id = Column(String(36), ForeignKey("logical_devices.id"), nullable=False)
    target_logical_device_id = Column(String(36), ForeignKey("logical_devices.id"))
    observed_identity_id = Column(String(36), ForeignKey("observed_identities.id"))
    correlation_id = Column(String(36), ForeignKey("device_identity_correlations.id"))
    reason = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DeviceIdentityCorrelation(Base):
    """An auditable relationship between two observed BLE identities.

    A relationship records its evidence and disposition independently from the
    logical-device state.  Statistical links remain proposals unless an
    explicitly approved acceptance policy promotes them.
    """

    __tablename__ = "device_identity_correlations"

    id = Column(String(36), primary_key=True, default=uuid_str)
    predecessor_identity_id = Column(String(36), ForeignKey("observed_identities.id"), nullable=False)
    successor_identity_id = Column(String(36), ForeignKey("observed_identities.id"), nullable=False)
    predecessor_logical_device_id = Column(String(36), ForeignKey("logical_devices.id"), nullable=False)
    successor_logical_device_id = Column(String(36), ForeignKey("logical_devices.id"), nullable=False)
    method = Column(String(120), nullable=False)
    status = Column(String(40), nullable=False, default="proposal")
    time_difference_seconds = Column(Float)
    rssi_difference_db = Column(Float)
    assignment_cost = Column(Float)
    alpha = Column(Float)
    search_window_seconds = Column(Float)
    evaluation_window_seconds = Column(Float)
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    accepted_at = Column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "predecessor_identity_id",
            "successor_identity_id",
            "method",
            name="uq_identity_correlation_pair_method",
        ),
        Index("ix_identity_correlation_successor_status", "successor_identity_id", "status"),
        Index("ix_identity_correlation_logical_status", "predecessor_logical_device_id", "status"),
    )


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String(120), primary_key=True)
    value = Column(JSON, nullable=False)
    description = Column(Text)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ProcessingError(Base):
    __tablename__ = "processing_errors"

    id = Column(String(36), primary_key=True, default=uuid_str)
    scanner_id = Column(String(64), ForeignKey("scanners.id"))
    batch_id = Column(String(120))
    observation_id = Column(String(120))
    error_category = Column(String(120), nullable=False)
    message = Column(Text, nullable=False)
    payload_excerpt = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_processing_errors_created", "created_at"),
    )
