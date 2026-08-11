from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.app.config import Settings
from backend.app.database import Base
from backend.app.models import (
    DeviceEnrichment,
    DeviceEvent,
    DeviceLocationEstimate,
    DeviceTrackingPosition,
    DeviceTrackingSample,
    DeviceTrackingScanner,
    DeviceTrackingSession,
    LogicalDevice,
    Observation,
    ObservedIdentity,
    ProcessingError,
    Scanner,
    ScannerConfiguration,
    ScannerHeartbeat,
    SystemSetting,
)
from backend.app.processing import utcnow
from backend.app.retention import cleanup_retained_history
from backend.app.schemas import SettingsPatchIn
from backend.app.services import (
    ensure_default_settings,
    patch_settings,
    scanner_config_payload,
)


def retention_sessionmaker():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def count(db, model) -> int:
    return db.execute(select(func.count(model.id))).scalar_one()


def test_runtime_threshold_configuration_is_semantically_validated():
    with pytest.raises(ValueError, match="must exceed"):
        Settings(presence_missing_seconds=60, presence_offline_seconds=60)
    with pytest.raises(ValueError, match="at least raw"):
        Settings(raw_observation_retention_days=30, summary_retention_days=7)


def test_scanner_payload_only_contains_fields_consumed_by_firmware():
    config = ScannerConfiguration(
        version=7,
        scan_interval_ms=2500,
        upload_interval_seconds=4,
        batch_size=12,
        rssi_min=-100,
    )

    payload = scanner_config_payload(config, {"session_id": "tracking-1"})

    assert payload == {
        "version": 7,
        "scan_interval_ms": 2500,
        "upload_interval_seconds": 4,
        "batch_size": 12,
        "rssi_min": -100,
        "tracking_focus": {"session_id": "tracking-1"},
    }


def test_dynamic_settings_reject_unknown_keys_and_remove_server_owned_duplicates():
    Session = retention_sessionmaker()
    with Session() as db:
        db.add(
            SystemSetting(
                key="presence_missing_seconds",
                value=999,
                description="stale duplicate",
            )
        )
        db.commit()

        ensure_default_settings(db)

        assert db.get(SystemSetting, "presence_missing_seconds") is None
        with pytest.raises(ValueError, match="unsupported dynamic setting"):
            patch_settings(db, SettingsPatchIn(values={"unknown_setting": 1}))
        with pytest.raises(ValueError, match="must be between"):
            patch_settings(
                db,
                SettingsPatchIn(values={"correlation_rotation_window_seconds": 0}),
            )


def test_retention_cleans_all_history_tiers_and_preserves_current_state():
    Session = retention_sessionmaker()
    now = utcnow()
    old_raw = now - timedelta(days=10)
    old_summary = now - timedelta(days=40)
    with Session() as db:
        scanner = Scanner(
            id="scn-retention",
            display_name="Retention scanner",
            hardware_id="retention-hardware",
            token_hash="hash",
            status="online",
            enabled=True,
        )
        device = LogicalDevice(
            id="dev-retention",
            primary_address="aa:bb:cc:dd:ee:10",
            primary_address_type="public",
            status="active",
            first_seen_at=old_summary,
            last_seen_at=now,
            observation_count=2,
        )
        identity = ObservedIdentity(
            id="identity-retention",
            address="aa:bb:cc:dd:ee:10",
            address_type="public",
            randomized_address=False,
            first_seen_at=old_summary,
            last_seen_at=now,
            observation_count=2,
        )
        db.add_all([scanner, device, identity])
        db.flush()
        db.add_all(
            [
                Observation(
                    scanner_id=scanner.id,
                    batch_id="batch-old",
                    observation_id="observation-old",
                    observed_identity_id=identity.id,
                    logical_device_id=device.id,
                    observed_at=old_raw,
                    server_received_at=old_raw,
                    processed_at=old_raw,
                    rssi=-80,
                ),
                Observation(
                    scanner_id=scanner.id,
                    batch_id="batch-new",
                    observation_id="observation-new",
                    observed_identity_id=identity.id,
                    logical_device_id=device.id,
                    observed_at=now,
                    server_received_at=now,
                    processed_at=now,
                    rssi=-70,
                ),
                DeviceLocationEstimate(
                    logical_device_id=device.id,
                    scanner_id=scanner.id,
                    estimated_at=old_raw,
                ),
                DeviceLocationEstimate(
                    logical_device_id=device.id,
                    scanner_id=scanner.id,
                    estimated_at=now,
                ),
                ScannerHeartbeat(
                    scanner_id=scanner.id,
                    message_id="heartbeat-old",
                    received_at=old_raw,
                ),
                ScannerHeartbeat(
                    scanner_id=scanner.id,
                    message_id="heartbeat-new",
                    received_at=now,
                ),
                DeviceEnrichment(
                    logical_device_id=device.id,
                    observed_identity_id=identity.id,
                    scanner_id=scanner.id,
                    source_observation_id="observation-old",
                    enriched_at=old_summary,
                    status="success",
                ),
                DeviceEnrichment(
                    logical_device_id=device.id,
                    observed_identity_id=identity.id,
                    scanner_id=scanner.id,
                    source_observation_id="observation-new",
                    enriched_at=now,
                    status="success",
                ),
                DeviceEvent(
                    event_type="device_discovered",
                    logical_device_id=device.id,
                    occurred_at=old_summary,
                ),
                DeviceEvent(
                    event_type="device_returned",
                    logical_device_id=device.id,
                    occurred_at=now,
                ),
                ProcessingError(
                    error_category="old",
                    message="old error",
                    created_at=old_summary,
                ),
                ProcessingError(
                    error_category="new",
                    message="new error",
                    created_at=now,
                ),
            ]
        )
        old_session = DeviceTrackingSession(
            id="tracking-old",
            logical_device_id=device.id,
            state="stopped",
            started_at=old_summary,
            last_lease_at=old_summary,
            expires_at=old_summary,
            ended_at=old_summary,
        )
        active_session = DeviceTrackingSession(
            id="tracking-active",
            logical_device_id=device.id,
            state="live",
            started_at=now,
            last_lease_at=now,
            expires_at=now + timedelta(minutes=1),
        )
        db.add_all([old_session, active_session])
        db.flush()
        old_assignment = DeviceTrackingScanner(
            id="assignment-old",
            session_id=old_session.id,
            scanner_id=scanner.id,
            state="stopped",
        )
        db.add(old_assignment)
        db.flush()
        db.add_all(
            [
                DeviceTrackingSample(
                    session_id=old_session.id,
                    assignment_id=old_assignment.id,
                    scanner_id=scanner.id,
                    observed_identity_id=identity.id,
                    batch_id="tracking-batch-old",
                    sample_id="tracking-sample-old",
                    observed_at=old_raw,
                    server_received_at=old_raw,
                    boot_id="boot-old",
                    monotonic_ms=1,
                    sequence=1,
                    address=identity.address,
                    address_type="public",
                    rssi=-80,
                    smoothed_rssi=-80,
                    signal_level=0.1,
                ),
                DeviceTrackingPosition(
                    session_id=old_session.id,
                    scanner_id=scanner.id,
                    position_id="tracking-position-old",
                    observed_at=old_raw,
                    server_received_at=old_raw,
                    latitude=-6.2,
                    longitude=106.8,
                    accuracy_m=10,
                ),
            ]
        )
        db.commit()

        result = cleanup_retained_history(
            db,
            Settings(
                raw_observation_retention_days=7,
                summary_retention_days=30,
            ),
        )

        assert result == {
            "observations": 1,
            "location_estimates": 1,
            "heartbeats": 1,
            "tracking_samples": 1,
            "tracking_positions": 1,
            "device_enrichments": 1,
            "events": 1,
            "processing_errors": 1,
            "tracking_sessions": 1,
        }
        assert count(db, Observation) == 1
        assert count(db, DeviceLocationEstimate) == 1
        assert count(db, ScannerHeartbeat) == 1
        assert count(db, DeviceEnrichment) == 1
        assert count(db, DeviceEvent) == 1
        assert count(db, ProcessingError) == 1
        assert count(db, DeviceTrackingSession) == 1
        assert count(db, DeviceTrackingScanner) == 0
        assert count(db, LogicalDevice) == 1
        assert count(db, ObservedIdentity) == 1
