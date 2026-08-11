from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.app.database import Base
from backend.app.models import (
    DeviceTrackingPosition,
    DeviceTrackingSample,
    DeviceTrackingScanner,
    DeviceTrackingSession,
    LogicalDevice,
    Observation,
    ObservedIdentity,
    Scanner,
    ScannerConfiguration,
)
from backend.app.processing import utcnow
from backend.app.schemas import TrackingPositionIn, TrackingSampleBatchIn, TrackingSessionCreateIn
from backend.app.tracking import (
    TrackingConflictError,
    TrackingValidationError,
    get_tracking_session,
    ingest_tracking_samples,
    record_tracking_position,
    renew_tracking_lease,
    start_tracking_session,
    stop_tracking_session,
    tracking_focus_for_scanner,
)


def tracking_sessionmaker():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def seed_tracking_device(db, *, with_observation: bool = True):
    now = utcnow()
    scanner = Scanner(
        id="scn_tracking",
        display_name="Tracking Scanner",
        hardware_id="tracking-hardware",
        token_hash="hash",
        status="online",
        enabled=True,
        latitude=-6.2,
        longitude=106.8,
    )
    device = LogicalDevice(
        id="dev_tracking",
        primary_address="aa:bb:cc:dd:ee:01",
        primary_address_type="public",
        display_name="Observed Headset",
        status="active",
        movement_status="stationary",
        current_scanner_id=scanner.id,
        latitude=-6.2,
        longitude=106.8,
        first_seen_at=now,
        last_seen_at=now,
        observation_count=1 if with_observation else 0,
    )
    db.add_all([scanner, device])
    db.flush()
    db.add(ScannerConfiguration(scanner_id=scanner.id))
    identity = ObservedIdentity(
        id="identity_tracking",
        address="aa:bb:cc:dd:ee:01",
        address_type="public",
        randomized_address=False,
        first_seen_at=now,
        last_seen_at=now,
        observation_count=1,
    )
    db.add(identity)
    db.flush()
    if with_observation:
        db.add(
            Observation(
                scanner_id=scanner.id,
                batch_id="batch-observed",
                observation_id="obs-observed",
                observed_identity_id=identity.id,
                logical_device_id=device.id,
                observed_at=now,
                server_received_at=now,
                processed_at=now,
                rssi=-68,
            )
        )
    db.commit()
    return scanner, device, identity


def focus_batch(session_id: str, *, address: str = "aa:bb:cc:dd:ee:01", sequence: int = 1):
    return TrackingSampleBatchIn(
        batch_id=f"focus-batch-{sequence}",
        session_id=session_id,
        samples=[
            {
                "sample_id": f"focus-sample-{sequence}",
                "observed_at": utcnow(),
                "boot_id": "boot-focus",
                "monotonic_ms": sequence * 200,
                "sequence": sequence,
                "address": address,
                "address_type": "public",
                "rssi": -64,
            }
        ],
    )


def test_tracking_session_requires_real_ble_observation():
    Session = tracking_sessionmaker()
    with Session() as db:
        _scanner, device, _identity = seed_tracking_device(db, with_observation=False)

        with pytest.raises(TrackingValidationError, match="real BLE observation"):
            start_tracking_session(db, device.id, TrackingSessionCreateIn(mode="fixed"))

        assert db.execute(select(func.count(DeviceTrackingSession.id))).scalar_one() == 0


def test_tracking_focus_uses_observed_identity_and_scanner_snapshot():
    Session = tracking_sessionmaker()
    with Session() as db:
        scanner, device, identity = seed_tracking_device(db)

        session = start_tracking_session(db, device.id, TrackingSessionCreateIn(mode="fixed"))
        focus = tracking_focus_for_scanner(db, scanner.id)

        assert session["state"] == "arming"
        assert session["assignments"][0]["fixed_latitude"] == -6.2
        assert focus["session_id"] == session["id"]
        assert focus["target_identities"] == [
            {
                "observed_identity_id": identity.id,
                "address": identity.address,
                "address_type": identity.address_type,
            }
        ]


def test_restarting_same_device_tracking_session_is_idempotent():
    Session = tracking_sessionmaker()
    with Session() as db:
        _scanner, device, _identity = seed_tracking_device(db)

        first = start_tracking_session(db, device.id, TrackingSessionCreateIn(mode="walk"))
        second = start_tracking_session(db, device.id, TrackingSessionCreateIn(mode="fixed"))

        assert second["id"] == first["id"]
        assert second["mode"] == "walk"
        assert second["expires_at"] >= first["expires_at"]
        assert db.execute(select(func.count(DeviceTrackingSession.id))).scalar_one() == 1


def test_focus_sample_updates_presence_without_moving_location_anchor():
    Session = tracking_sessionmaker()
    with Session() as db:
        scanner, device, _identity = seed_tracking_device(db)
        session = start_tracking_session(db, device.id, TrackingSessionCreateIn(mode="fixed"))
        before = {
            "status": device.status,
            "movement_status": device.movement_status,
            "latitude": device.latitude,
            "longitude": device.longitude,
            "observation_count": device.observation_count,
            "last_seen_at": device.last_seen_at,
        }

        result = ingest_tracking_samples(db, scanner, focus_batch(session["id"]))
        duplicate = ingest_tracking_samples(db, scanner, focus_batch(session["id"]))
        db.refresh(device)

        assert result["accepted"] == 1
        assert result["state"] == "live"
        assert result["live_samples"][0]["signal_level"] == pytest.approx(0.525)
        assert duplicate["duplicates"] == 1
        assert db.execute(select(func.count(DeviceTrackingSample.id))).scalar_one() == 1
        assert device.status == before["status"]
        assert device.movement_status == before["movement_status"]
        assert device.latitude == before["latitude"]
        assert device.longitude == before["longitude"]
        assert device.observation_count == before["observation_count"] + 1
        assert device.last_seen_at >= before["last_seen_at"]


def test_focus_sample_returns_missing_device_without_reanchoring_it():
    Session = tracking_sessionmaker()
    with Session() as db:
        scanner, device, identity = seed_tracking_device(db)
        device.status = "temporarily_missing"
        device.latitude = -6.21
        device.longitude = 106.81
        db.commit()
        session = start_tracking_session(db, device.id, TrackingSessionCreateIn(mode="fixed"))

        result = ingest_tracking_samples(db, scanner, focus_batch(session["id"]))
        db.refresh(device)
        db.refresh(identity)

        assert result["accepted"] == 1
        assert device.status == "returned"
        assert (device.latitude, device.longitude) == (-6.21, 106.81)
        assert identity.observation_count == 2


def test_focus_batch_rejects_address_outside_selected_logical_device():
    Session = tracking_sessionmaker()
    with Session() as db:
        scanner, device, _identity = seed_tracking_device(db)
        session = start_tracking_session(db, device.id, TrackingSessionCreateIn(mode="fixed"))

        result = ingest_tracking_samples(
            db,
            scanner,
            focus_batch(session["id"], address="aa:bb:cc:dd:ee:99"),
        )

        assert result["accepted"] == 0
        assert result["rejected"] == 1
        assert db.execute(select(func.count(DeviceTrackingSample.id))).scalar_one() == 0


def test_walk_position_is_session_evidence_and_does_not_patch_scanner():
    Session = tracking_sessionmaker()
    with Session() as db:
        scanner, device, _identity = seed_tracking_device(db)
        session = start_tracking_session(db, device.id, TrackingSessionCreateIn(mode="walk"))
        payload = TrackingPositionIn(
            position_id="position-1",
            scanner_id=scanner.id,
            observed_at=utcnow(),
            latitude=-6.21,
            longitude=106.81,
            accuracy_m=8.5,
        )

        position = record_tracking_position(db, session["id"], payload)
        duplicate = record_tracking_position(db, session["id"], payload)
        db.refresh(scanner)

        assert position["id"] == duplicate["id"]
        assert db.execute(select(func.count(DeviceTrackingPosition.id))).scalar_one() == 1
        assert scanner.latitude == -6.2
        assert scanner.longitude == 106.8


def test_fixed_session_rejects_walk_position():
    Session = tracking_sessionmaker()
    with Session() as db:
        scanner, device, _identity = seed_tracking_device(db)
        session = start_tracking_session(db, device.id, TrackingSessionCreateIn(mode="fixed"))

        with pytest.raises(TrackingConflictError, match="walk mode"):
            record_tracking_position(
                db,
                session["id"],
                TrackingPositionIn(
                    position_id="position-fixed",
                    scanner_id=scanner.id,
                    observed_at=utcnow(),
                    latitude=-6.2,
                    longitude=106.8,
                    accuracy_m=5,
                ),
            )


def test_lease_and_stop_remove_focus_and_preserve_summary():
    Session = tracking_sessionmaker()
    with Session() as db:
        scanner, device, _identity = seed_tracking_device(db)
        session = start_tracking_session(db, device.id, TrackingSessionCreateIn(mode="fixed"))
        ingest_tracking_samples(db, scanner, focus_batch(session["id"]))

        renewed = renew_tracking_lease(db, session["id"])
        stopped = stop_tracking_session(db, session["id"])

        assert renewed["expires_at"] > renewed["last_lease_at"]
        assert stopped["state"] == "stopped"
        assert stopped["summary"]["sample_count"] == 1
        assert stopped["summary"]["maximum_rssi"] == -64
        assert tracking_focus_for_scanner(db, scanner.id) is None
        assert get_tracking_session(db, session["id"])["state"] == "stopped"


def test_in_flight_batch_after_stop_is_acknowledged_and_discarded():
    Session = tracking_sessionmaker()
    with Session() as db:
        scanner, device, _identity = seed_tracking_device(db)
        session = start_tracking_session(db, device.id, TrackingSessionCreateIn(mode="fixed"))
        stop_tracking_session(db, session["id"])

        result = ingest_tracking_samples(db, scanner, focus_batch(session["id"]))

        assert result == {
            "accepted": 0,
            "duplicates": 0,
            "rejected": 0,
            "discarded": 1,
            "session_id": session["id"],
            "state": "stopped",
            "live_samples": [],
        }
        assert db.execute(select(func.count(DeviceTrackingSample.id))).scalar_one() == 0


def test_late_sample_is_stored_but_not_emitted_as_live():
    Session = tracking_sessionmaker()
    with Session() as db:
        scanner, device, _identity = seed_tracking_device(db)
        session = start_tracking_session(db, device.id, TrackingSessionCreateIn(mode="fixed"))
        payload = focus_batch(session["id"])
        payload.samples[0].observed_at = utcnow() - timedelta(seconds=20)

        result = ingest_tracking_samples(db, scanner, payload)
        stored = db.execute(select(DeviceTrackingSample)).scalar_one()

        assert result["accepted"] == 1
        assert result["live_samples"] == []
        assert stored.delayed is True
        assignment = db.execute(select(DeviceTrackingScanner)).scalar_one()
        assert assignment.last_sample_at is None
        assert assignment.state == "arming"


def test_usb_transport_latency_within_minimum_adaptive_window_remains_live():
    Session = tracking_sessionmaker()
    with Session() as db:
        scanner, device, _identity = seed_tracking_device(db)
        session = start_tracking_session(db, device.id, TrackingSessionCreateIn(mode="fixed"))
        payload = focus_batch(session["id"])
        payload.samples[0].observed_at = utcnow() - timedelta(seconds=8)

        result = ingest_tracking_samples(db, scanner, payload)
        stored = db.execute(select(DeviceTrackingSample)).scalar_one()

        assert result["accepted"] == 1
        assert len(result["live_samples"]) == 1
        assert stored.delayed is False


def test_tracking_uses_four_second_time_window_median():
    Session = tracking_sessionmaker()
    with Session() as db:
        scanner, device, _identity = seed_tracking_device(db)
        session = start_tracking_session(db, device.id, TrackingSessionCreateIn(mode="fixed"))

        for sequence, rssi in enumerate([-90, -60, -61], start=1):
            payload = focus_batch(session["id"], sequence=sequence)
            payload.samples[0].rssi = rssi
            ingest_tracking_samples(db, scanner, payload)

        samples = db.execute(
            select(DeviceTrackingSample).order_by(DeviceTrackingSample.sequence)
        ).scalars().all()

        assert [sample.smoothed_rssi for sample in samples] == [-90.0, -75.0, -61.0]


def test_tracking_stale_window_adapts_to_observed_advertising_cadence():
    Session = tracking_sessionmaker()
    with Session() as db:
        scanner, device, identity = seed_tracking_device(db)
        session = start_tracking_session(db, device.id, TrackingSessionCreateIn(mode="fixed"))
        assignment = db.execute(select(DeviceTrackingScanner)).scalar_one()
        started_at = utcnow() - timedelta(seconds=50)
        for sequence in range(6):
            observed_at = started_at + timedelta(seconds=sequence * 8)
            db.add(
                DeviceTrackingSample(
                    session_id=session["id"],
                    assignment_id=assignment.id,
                    scanner_id=scanner.id,
                    observed_identity_id=identity.id,
                    batch_id=f"adaptive-batch-{sequence}",
                    sample_id=f"adaptive-sample-{sequence}",
                    observed_at=observed_at,
                    server_received_at=observed_at + timedelta(seconds=2),
                    boot_id="boot-adaptive",
                    monotonic_ms=sequence * 8000,
                    sequence=sequence,
                    address=identity.address,
                    address_type=identity.address_type,
                    rssi=-65,
                    smoothed_rssi=-65.0,
                    signal_level=0.5,
                    delayed=False,
                )
            )
        db.commit()

        hydrated = get_tracking_session(db, session["id"])

        assert hydrated["sample_stale_seconds"] == 16
        assert hydrated["assignments"][0]["sample_stale_seconds"] == 16
        assert hydrated["signal_scale"]["filter"] == "time_window_median"
