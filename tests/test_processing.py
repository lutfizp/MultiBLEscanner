from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.app.bluetooth_sig import (
    company_identifier_from_manufacturer_data,
    company_identifier_hex,
    company_name_from_manufacturer_data,
)
from backend.app.bluetooth_ad import parse_advertising_and_scan_response
from backend.app.database import SQLITE_BUSY_TIMEOUT_SECONDS, SQLITE_POOL_SIZE, Base, engine_options
from backend.app.device_intelligence import analyze_manufacturer_data, infer_device_category, short_uuid
from backend.app.models import DeviceEvent
from backend.app.processing import (
    ProcessingSettings,
    ensure_utc,
    evaluate_presence_status,
    identity_signature,
    infer_proximity_from_rssi,
    is_randomized_address,
    is_synthetic_address_pattern,
    normalize_hex,
    observed_again_status,
    proximity_band,
    rssi_window_metrics,
    signal_band_from_rssi,
    utcnow,
)
from backend.app.schemas import (
    BLEObservationIn,
    BrowserLocationDiagnosticIn,
    GATTEnrichmentIn,
    HeartbeatIn,
    ObservationBatchIn,
    ScannerPositionIn,
)
from backend.app.services import create_event, serialize_datetime


def test_rssi_signal_bands_do_not_claim_distance():
    assert signal_band_from_rssi(-55) == "signal_strong"
    assert signal_band_from_rssi(-70) == "signal_moderate"
    assert signal_band_from_rssi(-82) == "signal_weak"
    assert signal_band_from_rssi(-94) == "signal_very_weak"
    assert proximity_band(None, -70) == "signal_moderate"


def test_browser_location_diagnostic_contains_no_coordinate_fields():
    payload = BrowserLocationDiagnosticIn(
        recorded_at=utcnow(),
        stage="timeout",
        page_origin="https://localhost:8000",
        secure_context=True,
        permission_state="granted",
        watcher_active=True,
        visibility_state="visible",
        error_code=3,
        error_message="location delayed",
    )

    serialized = payload.model_dump(mode="json")
    assert "latitude" not in serialized
    assert "longitude" not in serialized


def test_sqlite_engine_uses_bounded_wal_friendly_pool_options():
    options = engine_options("sqlite:////tmp/bluetooth-scanner.sqlite3")

    assert options["pool_size"] == SQLITE_POOL_SIZE
    assert options["pool_size"] > 1
    assert options["max_overflow"] == 0
    assert options["pool_timeout"] == SQLITE_BUSY_TIMEOUT_SECONDS
    assert options["connect_args"]["timeout"] == SQLITE_BUSY_TIMEOUT_SECONDS


def test_journal_distance_model_is_explicit_and_bounded_by_provenance():
    result = infer_proximity_from_rssi(-75)

    assert result.band == "signal_moderate"
    assert result.distance_m == pytest.approx(25.12, rel=1e-3)
    assert result.distance_range_m is None
    assert result.probabilities == {}
    assert result.method == "journal_esp32_log_distance_baseline_v1"
    assert result.confidence == 0.0


def test_paper_rssi_window_metric_uses_two_five_sample_windows():
    stable = rssi_window_metrics({"scanner-a": [-70] * 10})
    shifted = rssi_window_metrics({"scanner-a": [-80] * 5 + [-70] * 5})

    assert stable.window_ready is True
    assert stable.anchor_count == 1
    assert stable.weights == {"scanner-a": 1.0}
    assert stable.rssi_metric == 0.0
    assert stable.reliability == 1.0
    assert shifted.previous_window_means == {"scanner-a": -80.0}
    assert shifted.current_window_means == {"scanner-a": -70.0}
    assert shifted.absolute_changes_db == {"scanner-a": 10.0}
    assert shifted.rssi_metric == pytest.approx(1.0)
    assert shifted.reliability == pytest.approx(0.0)


def test_presence_thresholds_are_cautious():
    now = utcnow()
    settings = ProcessingSettings(presence_missing_seconds=45, presence_offline_seconds=180)

    assert evaluate_presence_status("active", now - timedelta(seconds=10), now, settings) is None
    assert evaluate_presence_status("active", now - timedelta(seconds=60), now, settings)[0] == "temporarily_missing"
    assert evaluate_presence_status("temporarily_missing", now - timedelta(seconds=240), now, settings)[0] == "offline"
    assert observed_again_status("offline")[0] == "returned"


def test_randomized_address_depends_on_address_type_not_guessing():
    assert is_randomized_address("random_private", "c0:98:00:00:00:01") is True
    assert is_randomized_address("public", "c0:98:00:00:00:01") is False
    assert is_randomized_address(None, "c0:98:00:00:00:01") is False


def test_synthetic_address_patterns_are_rejected_conservatively():
    repeated_octet = ":".join(["00"] * 6)
    uniform_step = ":".join(f"{value:02x}" for value in range(0x11, 0x77, 0x11))

    assert is_synthetic_address_pattern(repeated_octet) is True
    assert is_synthetic_address_pattern(uniform_step) is True
    assert is_synthetic_address_pattern("cf:28:3b:81:15:b3") is False
    assert is_synthetic_address_pattern("1e:0a:a4:45:af:75") is False
    assert is_synthetic_address_pattern("not-a-mac") is False


def test_synthetic_observation_is_not_stored():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import LogicalDevice, Observation, ObservedIdentity, Scanner, ScannerConfiguration
    from backend.app.services import process_batch

    with TestSession() as db:
        scanner = Scanner(
            id="scn_synthetic_guard",
            display_name="Synthetic Guard Scanner",
            hardware_id="synthetic-guard-001",
            token_hash="hash",
            enabled=True,
        )
        db.add(scanner)
        db.flush()
        db.add(ScannerConfiguration(scanner_id=scanner.id))
        db.commit()

        result = process_batch(
            db,
            scanner,
            ObservationBatchIn(
                batch_id="batch-synthetic-guard",
                observations=[
                    {
                        "observation_id": "obs-synthetic-guard",
                        "address": "11:22:33:44:55:66",
                        "address_type": "public",
                        "rssi": -70,
                    }
                ],
            ),
        )

        assert result["accepted"] == 0
        assert result["ignored"] == 1
        assert db.execute(select(func.count(Observation.id))).scalar_one() == 0
        assert db.execute(select(func.count(ObservedIdentity.id))).scalar_one() == 0
        assert db.execute(select(func.count(LogicalDevice.id))).scalar_one() == 0


def test_scan_data_reset_preserves_scanner_setup_and_clears_runtime_state():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import Scanner, ScannerConfiguration, ScannerHeartbeat
    from backend.app.seed import clear_scan_data_in_session

    with TestSession() as db:
        scanner = Scanner(
            id="scn_reset_guard",
            display_name="Reset Guard Scanner",
            hardware_id="reset-guard-001",
            token_hash="hash",
            enabled=True,
            status="online",
            last_connection_at=utcnow(),
            last_heartbeat_at=utcnow(),
            last_seen_at=utcnow(),
            uptime_seconds=123,
            network_info={"wifi_connected": True},
        )
        db.add(scanner)
        db.flush()
        db.add(ScannerConfiguration(scanner_id=scanner.id))
        db.add(ScannerHeartbeat(scanner_id=scanner.id, message_id="heartbeat-before-reset"))
        db.add(DeviceEvent(event_type="scanner_connected", occurred_at=utcnow()))
        db.commit()

        deleted = clear_scan_data_in_session(db)
        db.commit()
        db.refresh(scanner)

        assert deleted["heartbeats"] == 1
        assert deleted["events"] == 1
        assert deleted["scanner_runtime_resets"] == 1
        assert scanner.status == "registered"
        assert scanner.last_connection_at is None
        assert scanner.last_heartbeat_at is None
        assert scanner.last_seen_at is None
        assert scanner.network_info == {}
        assert (
            db.execute(
                select(ScannerConfiguration).where(ScannerConfiguration.scanner_id == scanner.id)
            ).scalar_one()
            is not None
        )
        assert db.execute(select(ScannerConfiguration)).scalar_one().rssi_min == -110


def test_present_device_filter_excludes_missing_and_offline_history():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import LogicalDevice
    from backend.app.services import list_devices

    now = utcnow()
    with TestSession() as db:
        for index, status in enumerate(("newly_detected", "active", "returned", "temporarily_missing", "offline")):
            db.add(
                LogicalDevice(
                    primary_address=f"aa:bb:cc:dd:ee:{index:02x}",
                    primary_address_type="public",
                    display_name=f"Device {index}",
                    status=status,
                    movement_status="stationary",
                    first_seen_at=now,
                    last_seen_at=now,
                    observation_count=1,
                    identity_signature={},
                )
            )
        db.commit()

        present = list_devices(db, status="present")

        assert {device["status"] for device in present} == {"newly_detected", "active", "returned"}
        assert len(list_devices(db)) == 5


def test_unresolved_random_address_expires_without_becoming_offline_device():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.config import Settings
    from backend.app.models import LogicalDevice
    from backend.app.services import list_devices, overview, refresh_presence_states

    seen_at = utcnow() - timedelta(minutes=10)
    with TestSession() as db:
        unresolved = LogicalDevice(
            primary_address="5b:ff:fa:8b:66:a4",
            primary_address_type="random",
            display_name="5b:ff:fa:8b:66:a4",
            status="offline",
            movement_status="stationary",
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            observation_count=1,
            identity_signature={},
        )
        stable = LogicalDevice(
            primary_address="24:11:11:b3:eb:ee",
            primary_address_type="public",
            display_name="TWS",
            status="offline",
            movement_status="stationary",
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            observation_count=4,
            identity_signature={},
        )
        confirmed_random = LogicalDevice(
            alias="Known phone",
            primary_address="6a:11:22:33:44:55",
            primary_address_type="random",
            display_name="Phone",
            status="offline",
            movement_status="stationary",
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            observation_count=4,
            identity_signature={},
        )
        db.add_all([unresolved, stable, confirmed_random])
        db.commit()

        events = refresh_presence_states(
            db,
            Settings(presence_missing_seconds=45, presence_offline_seconds=180),
        )

        db.refresh(unresolved)
        assert unresolved.status == "identity_expired"
        assert [event.event_type for event in events] == ["device_identity_expired"]
        assert {device["display_name"] for device in list_devices(db)} == {"TWS", "Known phone"}
        assert len(list_devices(db, include_expired=True)) == 3
        assert len(list_devices(db, status="identity_expired")) == 1
        summary = overview(db)
        assert summary["offline_device_records"] == 2
        assert summary["expired_random_identities"] == 1
        assert refresh_presence_states(
            db,
            Settings(presence_missing_seconds=45, presence_offline_seconds=180),
        ) == []


def test_overview_separates_present_ble_records_from_trackable_devices():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import LogicalDevice
    from backend.app.services import list_devices, overview

    now = utcnow()
    with TestSession() as db:
        db.add_all(
            [
                LogicalDevice(
                    primary_address="24:11:11:b3:eb:ee",
                    primary_address_type="public",
                    display_name="Stable TWS",
                    status="active",
                    movement_status="signal_stable",
                    first_seen_at=now,
                    last_seen_at=now,
                    observation_count=4,
                    identity_signature={},
                ),
                LogicalDevice(
                    primary_address="5b:ff:fa:8b:66:a4",
                    primary_address_type="random",
                    display_name="5b:ff:fa:8b:66:a4",
                    status="returned",
                    movement_status="probably_moving",
                    first_seen_at=now,
                    last_seen_at=now,
                    observation_count=1,
                    identity_signature={},
                ),
                LogicalDevice(
                    primary_address="7a:11:22:33:44:55",
                    primary_address_type="random",
                    display_name="Expired random identity",
                    status="identity_expired",
                    movement_status="stationary",
                    first_seen_at=now - timedelta(minutes=10),
                    last_seen_at=now - timedelta(minutes=10),
                    observation_count=1,
                    identity_signature={},
                ),
            ],
        )
        db.commit()

        summary = overview(db)

        visible = list_devices(db, include_expired=True, include_transient=False)

    assert summary["present_ble_records"] == 2
    assert summary["active_devices"] == 1
    assert summary["active_unresolved_identities"] == 1
    assert summary["visible_device_candidates"] == 1
    assert [device["display_name"] for device in visible] == ["Stable TWS"]


def test_named_random_advertiser_is_visible_without_becoming_durable_identity():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import Scanner, ScannerConfiguration
    from backend.app.services import list_devices, process_batch

    raw_advertising = "0201060b094e616d65642042756473"
    with TestSession() as db:
        scanner = Scanner(
            id="scn_named_random",
            display_name="Named Random Scanner",
            hardware_id="named-random-hardware",
            token_hash="hash",
            enabled=True,
        )
        db.add(scanner)
        db.flush()
        db.add(ScannerConfiguration(scanner_id=scanner.id))
        db.commit()

        process_batch(
            db,
            scanner,
            ObservationBatchIn(
                batch_id="batch-named-random",
                observations=[
                    {
                        "observation_id": "obs-named-random",
                        "address": "6a:12:34:56:78:90",
                        "address_type": "random",
                        "rssi": -92,
                        "raw_advertising_payload": raw_advertising,
                        "advertising_packet_length": len(raw_advertising) // 2,
                        "packet_length": len(raw_advertising) // 2,
                        "payload_layout_version": 2,
                    }
                ],
            ),
        )

        visible = list_devices(db, include_transient=False)

        assert len(visible) == 1
        assert visible[0]["display_name"] == "Named Buds"
        assert visible[0]["visibility_class"] == "named_broadcast_candidate"
        assert visible[0]["presence_trackable"] is False


def test_normalize_hex_rejects_invalid_payload():
    assert normalize_hex("0x0a ff") == "0aff"
    with pytest.raises(ValueError):
        normalize_hex("not-hex")


def test_ad_parser_keeps_advertising_and_scan_response_separate():
    advertising = "02010603030f1805ff4c000102"
    scan_response = "06094d6f75736504160f1864020af50319c103"

    parsed = parse_advertising_and_scan_response(advertising, scan_response)

    assert parsed["capture_complete"] is True
    assert parsed["advertising_payload_length"] == 13
    assert parsed["scan_response_payload_length"] == 19
    assert parsed["scan_response_captured"] is True
    assert parsed["fields"]["name"] == "Mouse"
    assert parsed["fields"]["service_uuids"] == ["180f"]
    assert parsed["fields"]["service_data"] == {"180f": ["64"]}
    assert parsed["fields"]["manufacturer_data"] == "4c000102"
    assert parsed["fields"]["tx_power"] == -11
    assert parsed["fields"]["appearance"] == "961"
    assert parsed["fields"]["advertising_flags"]["br_edr_not_supported"] is True
    assert [item["source"] for item in parsed["structures"][:3]] == [
        "advertising",
        "advertising",
        "advertising",
    ]
    assert parsed["structures"][3]["source"] == "scan_response"


def test_ad_parser_marks_truncated_structures_without_inventing_fields():
    parsed = parse_advertising_and_scan_response("050106", None)

    assert parsed["capture_complete"] is False
    assert parsed["fields"]["advertising_flags"] == {}
    assert parsed["errors"][0]["code"] == "truncated_ad_structure"


def test_observation_payload_rejects_half_byte_hex():
    with pytest.raises(ValueError, match="whole bytes"):
        BLEObservationIn(
            observation_id="obs-half-byte",
            rssi=-70,
            raw_advertising_payload="0",
        )


def test_separated_raw_payload_is_canonical_source_for_ingestion():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import Observation, Scanner, ScannerConfiguration
    from backend.app.services import device_detail, list_devices, process_batch

    advertising = "02010603030f1805ff4c000102"
    scan_response = "06094d6f75736504160f1864020af50319c103"
    seen_at = utcnow()
    with TestSession() as db:
        scanner = Scanner(
            id="scn_ad_capture",
            display_name="AD Capture Scanner",
            hardware_id="ad-capture-001",
            token_hash="hash",
            enabled=True,
        )
        db.add(scanner)
        db.flush()
        db.add(ScannerConfiguration(scanner_id=scanner.id))
        db.commit()

        result = process_batch(
            db,
            scanner,
            ObservationBatchIn(
                batch_id="batch-ad-capture",
                sent_at=seen_at,
                scanner_time=seen_at,
                time_source="usb_host_synchronized",
                boot_id="boot-ad-capture",
                batch_sequence=1,
                clock_sync_age_ms=10,
                observations=[
                    {
                        "observation_id": "obs-ad-capture",
                        "observed_at": seen_at,
                        "scanner_time": seen_at,
                        "time_source": "usb_host_synchronized",
                        "boot_id": "boot-ad-capture",
                        "monotonic_ms": 1234,
                        "scan_cycle": 1,
                        "clock_sync_age_ms": 10,
                        "address": "cf:28:3b:81:15:b3",
                        "address_type": "random",
                        "advertised_name": "incorrect firmware name",
                        "rssi": -70,
                        "raw_advertising_payload": advertising,
                        "raw_scan_response_payload": scan_response,
                        "packet_length": 32,
                        "advertising_packet_length": 13,
                        "scan_response_packet_length": 19,
                        "payload_layout_version": 2,
                    }
                ],
            ),
        )

        assert result["accepted"] == 1
        observation = db.execute(select(Observation)).scalar_one()
        identity = observation.observed_identity
        assert identity.local_name == "Mouse"
        assert identity.service_data == {"180f": ["64"]}
        assert identity.manufacturer_data == "4c000102"
        assert observation.tx_power == -11
        assert observation.appearance == "961"
        assert observation.processing_notes["capture_provenance"]["capture_status"] == "verified"
        assert observation.processing_notes["capture_provenance"]["ad_parser"]["scan_response_captured"] is True
        assert observation.processing_notes["time_provenance"]["time_quality"] == "trusted"

        devices = list_devices(db)
        assert devices[0]["vendor"] == "Apple, Inc."
        assert devices[0]["manufacturer_company_id"] == "0x004C"
        assert devices[0]["manufacturer_evidence"] == "raw_advertising_verified"
        detail = device_detail(db, devices[0]["id"])
        assert detail["observed_identities"][0]["manufacturer_profile"]["company_name"] == "Apple, Inc."

        retry = process_batch(
            db,
            scanner,
            ObservationBatchIn(
                batch_id="batch-ad-capture-retry",
                observations=[
                    {
                        "observation_id": "obs-ad-capture",
                        "address": "cf:28:3b:81:15:b3",
                        "address_type": "random",
                        "rssi": -70,
                    }
                ],
            ),
        )
        assert retry["accepted"] == 0
        assert retry["duplicates"] == 1
        assert db.execute(select(func.count(Observation.id))).scalar_one() == 1


def test_gatt_enrichment_is_directly_stored_and_preferred_for_display_name():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import DeviceEnrichment, Scanner, ScannerConfiguration
    from backend.app.services import device_detail, list_devices, process_batch

    with TestSession() as db:
        scanner = Scanner(
            id="scn_gatt",
            display_name="GATT Scanner",
            hardware_id="gatt-scanner-001",
            token_hash="hash",
            enabled=True,
        )
        db.add(scanner)
        db.flush()
        db.add(ScannerConfiguration(scanner_id=scanner.id))
        db.commit()

        result = process_batch(
            db,
            scanner,
            ObservationBatchIn(
                batch_id="batch-gatt",
                observations=[
                    {
                        "observation_id": "obs-gatt",
                        "address": "24:11:11:b3:eb:ee",
                        "address_type": "public",
                        "advertised_name": "broadcast-name",
                        "rssi": -60,
                        "advertising_type": "adv_ind",
                        "connectable": True,
                        "gatt_enrichment": {
                            "status": "success",
                            "device_name": "Space Travel",
                            "manufacturer_name": "MOONDROP",
                            "model_number": "TWS-01",
                            "firmware_revision": "1.2.3",
                            "discovered_services": ["1800", "180a"],
                            "characteristic_values": {
                                "2a00": "53706163652054726176656c",
                                "2a24": "5457532d3031",
                            },
                            "attempt_duration_ms": 412,
                        },
                    }
                ],
            ),
        )

        assert result["accepted"] == 1
        enrichment = db.execute(select(DeviceEnrichment)).scalar_one()
        assert enrichment.device_name == "Space Travel"
        assert enrichment.model_number == "TWS-01"
        assert enrichment.details["directly_read"] is True
        assert enrichment.details["pairing_forced"] is False

        device = list_devices(db)[0]
        assert device["display_name"] == "Space Travel"
        assert device["display_name_source"] == "ble_gatt_device_name"
        assert device["gatt_enrichment"]["manufacturer_name"] == "MOONDROP"

        detail = device_detail(db, device["id"])
        assert detail["device_enrichments"][0]["transport"] == "ble_gatt"
        assert detail["recent_observations"][0]["gatt_enrichment"]["status"] == "success"


@pytest.mark.parametrize("status", ["operation_timeout", "cancelled"])
def test_gatt_terminal_worker_statuses_are_valid_direct_evidence(status):
    enrichment = GATTEnrichmentIn(
        status=status,
        error_code=f"test_{status}",
        attempt_duration_ms=15_000,
    )

    assert enrichment.status == status
    assert enrichment.device_name is None
    assert enrichment.error_code == f"test_{status}"


def test_live_processing_persists_paper_rssi_window_evidence():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import Scanner, ScannerConfiguration
    from backend.app.services import list_devices, process_batch

    with TestSession() as db:
        scanner = Scanner(
            id="scn_rssi_metric",
            display_name="RSSI Metric Scanner",
            hardware_id="rssi-metric-001",
            token_hash="hash",
            enabled=True,
        )
        db.add(scanner)
        db.flush()
        db.add(ScannerConfiguration(scanner_id=scanner.id))
        db.commit()

        for index in range(10):
            result = process_batch(
                db,
                scanner,
                ObservationBatchIn(
                    batch_id=f"batch-rssi-metric-{index}",
                    observations=[
                        {
                            "observation_id": f"obs-rssi-metric-{index}",
                            "address": "10:20:30:40:50:60",
                            "address_type": "public",
                            "rssi": -70,
                        }
                    ],
                ),
            )
            assert result["accepted"] == 1

        device = list_devices(db)[0]
        model = device["proximity_model"]
        assert model["method"] == "journal_esp32_log_distance_baseline_v1"
        assert model["window_ready"] is True
        assert model["window_size"] == 5
        assert model["anchor_count"] == 1
        assert model["rssi_metric"] == 0.0
        assert model["signal_reliability"] == 1.0
        assert device["estimated_distance_m"] == pytest.approx(14.13, rel=1e-3)


def test_latest_device_lookups_only_return_the_newest_row_per_device():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import DeviceLocationEstimate, LogicalDevice, Observation, Scanner
    from backend.app.services import latest_location_estimates, latest_observations

    with TestSession() as db:
        now = utcnow()
        scanner = Scanner(
            id="scn_latest_lookup",
            display_name="Latest Lookup Scanner",
            hardware_id="latest-lookup-001",
            token_hash="hash",
        )
        device = LogicalDevice(
            primary_address="aa:bb:cc:dd:ee:ff",
            primary_address_type="public",
            status="active",
            movement_status="stationary",
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add_all([scanner, device])
        db.flush()

        older_observation = Observation(
            scanner_id=scanner.id,
            batch_id="batch-latest-old",
            observation_id="obs-latest-old",
            observed_identity_id="identity-latest-old",
            logical_device_id=device.id,
            observed_at=now - timedelta(seconds=10),
            server_received_at=now - timedelta(seconds=10),
            processed_at=now - timedelta(seconds=10),
            rssi=-82,
        )
        newer_observation = Observation(
            scanner_id=scanner.id,
            batch_id="batch-latest-new",
            observation_id="obs-latest-new",
            observed_identity_id="identity-latest-new",
            logical_device_id=device.id,
            observed_at=now,
            server_received_at=now,
            processed_at=now,
            rssi=-70,
        )
        older_location = DeviceLocationEstimate(
            scanner_id=scanner.id,
            logical_device_id=device.id,
            estimated_at=now - timedelta(seconds=10),
            proximity_band="weak",
            confidence=0.1,
        )
        newer_location = DeviceLocationEstimate(
            scanner_id=scanner.id,
            logical_device_id=device.id,
            estimated_at=now,
            proximity_band="near",
            confidence=0.7,
        )
        db.add_all([older_observation, newer_observation, older_location, newer_location])
        db.commit()

        latest_observation = latest_observations(db, [device.id])
        latest_location = latest_location_estimates(db, [device.id])

        assert latest_observation[device.id].id == newer_observation.id
        assert latest_location[device.id].id == newer_location.id


def test_legacy_scanner_company_data_is_not_presented_as_verified_sig_company():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import Scanner, ScannerConfiguration
    from backend.app.services import device_detail, list_devices, process_batch

    seen_at = utcnow()
    with TestSession() as db:
        scanner = Scanner(
            id="scn_legacy_company",
            display_name="Legacy Company Scanner",
            hardware_id="legacy-company-001",
            token_hash="hash",
            enabled=True,
        )
        db.add(scanner)
        db.flush()
        db.add(ScannerConfiguration(scanner_id=scanner.id))
        db.commit()

        result = process_batch(
            db,
            scanner,
            ObservationBatchIn(
                batch_id="batch-legacy-company",
                sent_at=seen_at,
                observations=[
                    {
                        "observation_id": "obs-legacy-company",
                        "address": "cf:28:3b:81:15:b3",
                        "address_type": "random",
                        "rssi": -70,
                        "manufacturer_data": "4c000102",
                        "raw_advertising_payload": "05ff4c000102",
                    }
                ],
            ),
        )

        assert result["accepted"] == 1
        devices = list_devices(db)
        assert devices[0]["vendor"] is None
        assert devices[0]["manufacturer_company_id"] is None
        assert devices[0]["manufacturer_evidence"] == "legacy_payload_layout_unverified"
        detail = device_detail(db, devices[0]["id"])
        profile = detail["observed_identities"][0]["manufacturer_profile"]
        assert profile["company_name"] is None
        assert detail["observed_identities"][0]["manufacturer_evidence"] == "legacy_payload_layout_unverified"


def test_matching_advertisements_do_not_auto_merge_random_addresses():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import LogicalDevice, Scanner, ScannerConfiguration
    from backend.app.services import process_batch

    with TestSession() as db:
        scanner = Scanner(
            id="scn_no_candidate",
            display_name="No Candidate Scanner",
            hardware_id="no-candidate-001",
            token_hash="hash",
            enabled=True,
        )
        db.add(scanner)
        db.flush()
        db.add(ScannerConfiguration(scanner_id=scanner.id))
        db.commit()

        for batch_id, observation_id, address in [
            ("batch-no-candidate-a", "obs-no-candidate-a", "cf:28:3b:81:15:b3"),
            ("batch-no-candidate-b", "obs-no-candidate-b", "c1:7d:8a:b9:7f:4e"),
        ]:
            process_batch(
                db,
                scanner,
                ObservationBatchIn(
                    batch_id=batch_id,
                    observations=[
                        {
                            "observation_id": observation_id,
                            "address": address,
                            "address_type": "random",
                            "advertised_name": "Same name",
                            "service_uuids": ["180f"],
                            "manufacturer_data": "4c000102",
                            "rssi": -70,
                        }
                    ],
                ),
            )

        assert db.execute(select(func.count(LogicalDevice.id))).scalar_one() == 2


def test_expired_clock_sync_falls_back_to_server_receive_time():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import Observation, Scanner, ScannerConfiguration
    from backend.app.services import process_batch

    reported_at = utcnow() - timedelta(hours=1)
    with TestSession() as db:
        scanner = Scanner(
            id="scn_expired_clock",
            display_name="Expired Clock Scanner",
            hardware_id="expired-clock-001",
            token_hash="hash",
            enabled=True,
        )
        db.add(scanner)
        db.flush()
        db.add(ScannerConfiguration(scanner_id=scanner.id))
        db.commit()

        received_before = utcnow()
        process_batch(
            db,
            scanner,
            ObservationBatchIn(
                batch_id="batch-expired-clock",
                sent_at=reported_at,
                time_source="usb_host_synchronized",
                boot_id="boot-expired-clock",
                clock_sync_age_ms=300_001,
                observations=[
                    {
                        "observation_id": "obs-expired-clock",
                        "observed_at": reported_at,
                        "time_source": "usb_host_synchronized",
                        "boot_id": "boot-expired-clock",
                        "monotonic_ms": 100,
                        "clock_sync_age_ms": 300_001,
                        "address": "cf:28:3b:81:15:b3",
                        "address_type": "random",
                        "rssi": -70,
                    }
                ],
            ),
        )

        observation = db.execute(select(Observation)).scalar_one()
        assert ensure_utc(observation.scanner_time) == reported_at
        assert ensure_utc(observation.observed_at) >= received_before
        assert observation.processing_notes["time_provenance"]["time_quality"] == "untrusted"
        assert observation.processing_notes["time_provenance"]["fallback_reason"] == "clock_sync_age_exceeded"


def test_ensure_utc_adds_timezone_to_naive_values():
    now = utcnow().replace(tzinfo=None)
    assert ensure_utc(now).tzinfo is not None


def test_bluetooth_sig_company_lookup_from_manufacturer_data():
    assert company_identifier_from_manufacturer_data("4c000102") == 0x004C
    assert company_identifier_hex("4c000102") == "0x004C"
    assert company_name_from_manufacturer_data("4c000102") == "Apple, Inc."
    assert company_name_from_manufacturer_data("59000102") == "Nordic Semiconductor ASA"


def test_device_category_inference_from_reference_patterns():
    assert short_uuid("00001812-0000-1000-8000-00805f9b34fb") == "1812"
    assert infer_device_category(None, ["00001812-0000-1000-8000-00805f9b34fb"]) == "peripheral_hid"
    assert infer_device_category("Galaxy SmartTag", []) == "beacon_smart_tag"
    assert infer_device_category("AirPods Pro", []) == "audio_headphones"


def test_find_my_payload_detection():
    find_my_payload = "4c00" + "121910" + ("00" * 23) + "00"
    profile = analyze_manufacturer_data(find_my_payload)

    assert profile["company_id"] == "0x004C"
    assert profile["company_name"] == "Apple, Inc."
    assert profile["find_my"]["payload_type"] == "registered"
    assert profile["find_my"]["battery_status"] == "full"
    assert infer_device_category(None, [], find_my_payload) == "beacon_airtag_find_my"


def test_create_event_dedupes_pending_events_before_commit():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    with TestSession() as db:
        first = create_event(db, "scanner_disconnected", utcnow(), dedupe_key="same-event")
        second = create_event(db, "scanner_disconnected", utcnow(), dedupe_key="same-event")

        assert first is not None
        assert second is None
        db.commit()
        event_count = db.execute(select(func.count(DeviceEvent.id))).scalar_one()
        assert event_count == 1


def test_usb_serial_empty_datetime_fields_are_accepted():
    heartbeat = HeartbeatIn(message_id="hb-1", scanner_time="")
    batch = ObservationBatchIn(
        batch_id="batch-1",
        sent_at="",
        scanner_time="",
        observations=[
            {
                "observation_id": "obs-1",
                "observed_at": "",
                "scanner_time": "",
                "address": "cf:28:3b:81:15:b3",
                "address_type": "public",
                "rssi": -70,
            }
        ],
    )

    assert heartbeat.scanner_time is None
    assert batch.sent_at is None
    assert batch.scanner_time is None
    assert batch.observations[0].observed_at is None
    assert batch.observations[0].scanner_time is None


def test_heartbeat_updates_scanner_hardware_provenance():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import Scanner
    from backend.app.services import record_heartbeat

    with TestSession() as db:
        scanner = Scanner(
            id="scn_hardware",
            display_name="Hardware Scanner",
            hardware_id="hardware-scanner-001",
            token_hash="hash",
            hardware_version="esp32",
            enabled=True,
        )
        db.add(scanner)
        db.commit()

        result = record_heartbeat(
            db,
            scanner,
            HeartbeatIn(
                message_id="hb-hardware-1",
                firmware_version="esp32-ble-scanner-1.4.1",
                hardware_version="esp32-d0wd-v3",
            ),
        )
        db.refresh(scanner)

        assert result == {"accepted": True, "duplicate": False}
        assert scanner.firmware_version == "esp32-ble-scanner-1.4.1"
        assert scanner.hardware_version == "esp32-d0wd-v3"


def test_usb_observation_reuses_scanner_time_when_observed_at_is_missing():
    reported_at = utcnow()
    batch = ObservationBatchIn(
        batch_id="batch-usb-fallback",
        sent_at=reported_at,
        time_source="usb_host_synchronized",
        boot_id="boot-usb-fallback",
        observations=[
            {
                "observation_id": "obs-usb-fallback",
                "scanner_time": reported_at,
                "time_source": "usb_host_synchronized",
                "boot_id": "boot-usb-fallback",
                "monotonic_ms": 123,
                "address": "cf:28:3b:81:15:b3",
                "address_type": "random",
                "rssi": -70,
            }
        ],
    )

    assert batch.observations[0].observed_at == reported_at


def test_api_datetime_serialization_marks_utc_explicitly():
    value = utcnow()

    assert serialize_datetime(value).endswith("Z")


def test_device_receives_scanner_coordinates_on_creation():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import LogicalDevice, Scanner, ScannerConfiguration
    from backend.app.services import serialize_device

    with TestSession() as db:
        from backend.app.security import hash_scanner_token

        scanner = Scanner(
            id="scn_test_gps",
            display_name="GPS Test Scanner",
            hardware_id="test-gps-001",
            token_hash=hash_scanner_token("tok", "salt"),
            status="online",
            latitude=-6.2088,
            longitude=106.8456,
        )
        db.add(scanner)
        db.flush()
        db.add(ScannerConfiguration(scanner_id=scanner.id))

        device = LogicalDevice(
            primary_address="aa:bb:cc:dd:ee:ff",
            primary_address_type="public",
            display_name="Test BLE Device",
            status="active",
            movement_status="stationary",
            current_scanner_id=scanner.id,
            latitude=scanner.latitude,
            longitude=scanner.longitude,
            first_seen_at=utcnow(),
            last_seen_at=utcnow(),
            observation_count=1,
            identity_signature={},
        )
        db.add(device)
        db.commit()

        assert device.latitude == pytest.approx(-6.2088)
        assert device.longitude == pytest.approx(106.8456)

        serialized = serialize_device(device)
        assert serialized["latitude"] == pytest.approx(-6.2088)
        assert serialized["longitude"] == pytest.approx(106.8456)


def test_offline_device_keeps_last_coordinates():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import LogicalDevice

    with TestSession() as db:
        device = LogicalDevice(
            primary_address="aa:bb:cc:00:11:22",
            primary_address_type="public",
            display_name="Offline Test",
            status="offline",
            movement_status="stationary",
            latitude=-6.2088,
            longitude=106.8456,
            first_seen_at=utcnow(),
            last_seen_at=utcnow(),
            observation_count=5,
            identity_signature={},
        )
        db.add(device)
        db.commit()

        assert device.status == "offline"
        assert device.latitude == pytest.approx(-6.2088)
        assert device.longitude == pytest.approx(106.8456)


def test_observation_batches_keep_scanner_online_when_heartbeat_is_stale():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.config import Settings
    from backend.app.models import Scanner
    from backend.app.services import refresh_scanner_states

    now = utcnow()
    with TestSession() as db:
        scanner = Scanner(
            id="scn_stale_hb",
            display_name="Stale Heartbeat Scanner",
            hardware_id="stale-hb-001",
            token_hash="hash",
            status="online",
            last_heartbeat_at=now - timedelta(seconds=300),
            last_seen_at=now,
            enabled=True,
        )
        db.add(scanner)
        db.commit()

        events = refresh_scanner_states(db, Settings(heartbeat_timeout_seconds=90))
        db.refresh(scanner)

        assert events == []
        assert scanner.status == "online"


def test_same_device_moves_to_new_scanner_location_and_keeps_location_history():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import DeviceLocationEstimate, LogicalDevice, Observation, Scanner, ScannerConfiguration
    from backend.app.services import device_detail, process_batch

    with TestSession() as db:
        scanner_a = Scanner(
            id="scn_location_a",
            display_name="Scanner Location A",
            hardware_id="location-a-001",
            token_hash="hash",
            status="online",
            latitude=-6.208800,
            longitude=106.845600,
            zone="Location A",
            enabled=True,
        )
        scanner_b = Scanner(
            id="scn_location_b",
            display_name="Scanner Location B",
            hardware_id="location-b-001",
            token_hash="hash",
            status="online",
            latitude=-6.300000,
            longitude=106.900000,
            zone="Location B",
            enabled=True,
        )
        db.add_all([scanner_a, scanner_b])
        db.flush()
        db.add_all([
            ScannerConfiguration(scanner_id=scanner_a.id),
            ScannerConfiguration(scanner_id=scanner_b.id),
        ])
        db.commit()

        seen_at_a = utcnow()
        first_batch = ObservationBatchIn(
            batch_id="batch-location-a",
            sent_at=seen_at_a,
            time_source="usb_host_synchronized",
            boot_id="boot-location-a",
            batch_sequence=1,
            clock_sync_age_ms=10,
            observations=[
                {
                    "observation_id": "obs-location-a",
                    "observed_at": seen_at_a,
                    "time_source": "usb_host_synchronized",
                    "boot_id": "boot-location-a",
                    "monotonic_ms": 1_000,
                    "clock_sync_age_ms": 10,
                    "address": "cf:28:3b:81:15:b3",
                    "address_type": "public",
                    "rssi": -70,
                }
            ],
        )
        process_batch(db, scanner_a, first_batch)

        first_device = db.execute(select(LogicalDevice)).scalar_one()
        assert first_device.latitude == pytest.approx(-6.208800)
        assert first_device.longitude == pytest.approx(106.845600)

        seen_at_b = seen_at_a + timedelta(seconds=10)
        second_batch = ObservationBatchIn(
            batch_id="batch-location-b",
            sent_at=seen_at_b,
            time_source="usb_host_synchronized",
            boot_id="boot-location-b",
            batch_sequence=1,
            clock_sync_age_ms=10,
            observations=[
                {
                    "observation_id": "obs-location-b",
                    "observed_at": seen_at_b,
                    "time_source": "usb_host_synchronized",
                    "boot_id": "boot-location-b",
                    "monotonic_ms": 1_000,
                    "clock_sync_age_ms": 10,
                    "address": "cf:28:3b:81:15:b3",
                    "address_type": "public",
                    "rssi": -68,
                }
            ],
        )
        process_batch(db, scanner_b, second_batch)

        delayed_batch = ObservationBatchIn(
            batch_id="batch-location-a-delayed",
            sent_at=seen_at_a + timedelta(seconds=5),
            time_source="usb_host_synchronized",
            boot_id="boot-location-a",
            batch_sequence=2,
            clock_sync_age_ms=10,
            observations=[
                {
                    "observation_id": "obs-location-a-delayed",
                    "observed_at": seen_at_a + timedelta(seconds=5),
                    "time_source": "usb_host_synchronized",
                    "boot_id": "boot-location-a",
                    "monotonic_ms": 6_000,
                    "clock_sync_age_ms": 10,
                    "address": "cf:28:3b:81:15:b3",
                    "address_type": "public",
                    "rssi": -72,
                }
            ],
        )
        process_batch(db, scanner_a, delayed_batch)

        devices = db.execute(select(LogicalDevice)).scalars().all()
        assert len(devices) == 1
        device = devices[0]
        assert device.current_scanner_id == scanner_b.id
        assert device.current_zone == "Location B"
        assert device.latitude == pytest.approx(-6.300000)
        assert device.longitude == pytest.approx(106.900000)
        assert ensure_utc(device.last_seen_at) == seen_at_b

        estimates = db.execute(
            select(DeviceLocationEstimate)
            .where(DeviceLocationEstimate.logical_device_id == device.id)
            .order_by(DeviceLocationEstimate.estimated_at)
        ).scalars().all()
        assert [estimate.scanner_id for estimate in estimates] == [scanner_a.id, scanner_a.id, scanner_b.id]
        assert [estimate.zone for estimate in estimates] == ["Location A", "Location A", "Location B"]

        detail = device_detail(db, device.id)
        assert detail is not None
        assert [entry["scanner_id"] for entry in detail["location_history"]] == [
            scanner_b.id,
            scanner_a.id,
            scanner_a.id,
        ]

        move_event = db.execute(
            select(DeviceEvent).where(
                DeviceEvent.logical_device_id == device.id,
                DeviceEvent.event_type == "device_location_changed",
            )
        ).scalar_one()
        assert move_event.details["previous_scanner_id"] == scanner_a.id
        assert move_event.details["current_scanner_id"] == scanner_b.id

        delayed_observation = db.execute(
            select(Observation).where(Observation.observation_id == "obs-location-a-delayed")
        ).scalar_one()
        assert delayed_observation.processing_notes["updates_current_location"] is False


def test_moving_scanner_updates_device_anchor_only_after_a_new_ble_observation():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import DeviceEvent, DeviceLocationEstimate, LogicalDevice, Scanner, ScannerConfiguration
    from backend.app.services import list_devices, process_batch

    with TestSession() as db:
        first_seen = utcnow()
        scanner = Scanner(
            id="scn_movable_anchor",
            display_name="Movable Scanner",
            hardware_id="movable-anchor-001",
            token_hash="hash",
            status="online",
            latitude=-6.226100,
            longitude=106.852900,
            zone="Tebet",
            location_source="browser_geolocation",
            location_observed_at=first_seen,
            location_accuracy_m=12.0,
            enabled=True,
        )
        db.add(scanner)
        db.flush()
        db.add(ScannerConfiguration(scanner_id=scanner.id))
        db.commit()

        first_batch = ObservationBatchIn(
            batch_id="batch-anchor-tebet",
            sent_at=first_seen,
            time_source="usb_host_synchronized",
            boot_id="boot-anchor",
            batch_sequence=1,
            clock_sync_age_ms=10,
            observations=[
                {
                    "observation_id": "obs-anchor-tebet",
                    "observed_at": first_seen,
                    "time_source": "usb_host_synchronized",
                    "boot_id": "boot-anchor",
                    "monotonic_ms": 1_000,
                    "clock_sync_age_ms": 10,
                    "address": "80:e1:26:9e:3e:e3",
                    "address_type": "public",
                    "rssi": -66,
                }
            ],
        )
        process_batch(db, scanner, first_batch)

        device = db.execute(select(LogicalDevice)).scalar_one()
        assert device.current_zone == "Tebet"
        assert device.latitude == pytest.approx(-6.226100)
        assert device.longitude == pytest.approx(106.852900)
        assert ensure_utc(device.location_anchor_observed_at) == first_seen

        second_seen = first_seen + timedelta(seconds=10)
        scanner.latitude = -6.238300
        scanner.longitude = 106.975600
        scanner.zone = "Bekasi"
        scanner.location_observed_at = second_seen
        scanner.location_accuracy_m = 8.0
        db.commit()

        db.refresh(device)
        assert device.current_zone == "Tebet"
        assert device.latitude == pytest.approx(-6.226100)
        assert device.longitude == pytest.approx(106.852900)
        assert ensure_utc(device.location_anchor_observed_at) == first_seen

        second_batch = ObservationBatchIn(
            batch_id="batch-anchor-same-scanner",
            sent_at=second_seen,
            time_source="usb_host_synchronized",
            boot_id="boot-anchor",
            batch_sequence=2,
            clock_sync_age_ms=10,
            observations=[
                {
                    "observation_id": "obs-anchor-same-scanner",
                    "observed_at": second_seen,
                    "time_source": "usb_host_synchronized",
                    "boot_id": "boot-anchor",
                    "monotonic_ms": 11_000,
                    "clock_sync_age_ms": 10,
                    "address": "80:e1:26:9e:3e:e3",
                    "address_type": "public",
                    "rssi": -64,
                }
            ],
        )
        process_batch(db, scanner, second_batch)

        device = db.execute(select(LogicalDevice)).scalar_one()
        assert device.current_scanner_id == scanner.id
        assert device.current_zone == "Bekasi"
        assert device.latitude == pytest.approx(-6.238300)
        assert device.longitude == pytest.approx(106.975600)
        assert ensure_utc(device.location_anchor_observed_at) == second_seen

        serialized = list_devices(db)[0]
        assert serialized["location_anchor"] == {
            "scanner_id": scanner.id,
            "zone": "Bekasi",
            "latitude": pytest.approx(-6.238300),
            "longitude": pytest.approx(106.975600),
            "anchored_at": second_seen.isoformat().replace("+00:00", "Z"),
            "source": "scanner_snapshot_at_observation",
            "scanner_location_source": "browser_geolocation",
            "scanner_location_observed_at": second_seen.isoformat().replace("+00:00", "Z"),
            "accuracy_m": pytest.approx(8.0),
            "update_policy": "latest_observation_with_current_scanner_position",
        }

        latest_estimate = db.execute(
            select(DeviceLocationEstimate).order_by(DeviceLocationEstimate.estimated_at.desc())
        ).scalars().first()
        assert latest_estimate.details["scanner_latitude"] == pytest.approx(-6.238300)
        assert latest_estimate.details["scanner_longitude"] == pytest.approx(106.975600)
        assert latest_estimate.details["scanner_position_current"] is True
        assert latest_estimate.details["updates_current_anchor"] is True

        location_events = db.execute(
            select(DeviceEvent).where(DeviceEvent.event_type == "device_location_changed")
        ).scalars().all()
        assert len(location_events) == 1
        assert location_events[0].reason == "observed_after_scanner_position_changed"
        assert location_events[0].details["previous_zone"] == "Tebet"
        assert location_events[0].details["current_zone"] == "Bekasi"


def test_live_scanner_position_is_monotonic_and_does_not_change_firmware_config():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import Scanner, ScannerConfiguration
    from backend.app.services import diagnostics, record_scanner_position

    with TestSession() as db:
        scanner = Scanner(
            id="scn_live_gps",
            display_name="Live GPS Scanner",
            hardware_id="live-gps-001",
            token_hash="hash",
            status="online",
            config_version=7,
            enabled=True,
        )
        db.add(scanner)
        db.flush()
        db.add(ScannerConfiguration(scanner_id=scanner.id, version=7))
        db.commit()

        first_seen = utcnow()
        applied = record_scanner_position(
            db,
            scanner.id,
            ScannerPositionIn(
                observed_at=first_seen,
                latitude=-6.226100,
                longitude=106.852900,
                accuracy_m=11.5,
            ),
        )

        assert applied is not None
        assert applied["position_applied"] is True
        assert applied["latitude"] == pytest.approx(-6.226100)
        assert applied["longitude"] == pytest.approx(106.852900)
        assert applied["location_source"] == "browser_geolocation"
        assert applied["location_accuracy_m"] == pytest.approx(11.5)
        assert applied["config_version"] == 7
        position_diagnostics = diagnostics(db)["scanner_positions"][0]
        assert position_diagnostics["scanner_id"] == scanner.id
        assert position_diagnostics["coordinates_available"] is True
        assert position_diagnostics["source"] == "browser_geolocation"
        assert position_diagnostics["accuracy_m"] == pytest.approx(11.5)

        stale = record_scanner_position(
            db,
            scanner.id,
            ScannerPositionIn(
                observed_at=first_seen - timedelta(seconds=1),
                latitude=-6.300000,
                longitude=107.000000,
                accuracy_m=3.0,
            ),
        )

        assert stale is not None
        assert stale["position_applied"] is False
        assert stale["latitude"] == pytest.approx(-6.226100)
        assert stale["longitude"] == pytest.approx(106.852900)
        assert stale["config_version"] == 7


def test_last_reported_browser_position_is_preserved_when_the_fix_is_not_fresh():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    from backend.app.models import DeviceLocationEstimate, LogicalDevice, Scanner, ScannerConfiguration
    from backend.app.services import process_batch

    with TestSession() as db:
        observed_at = utcnow()
        scanner = Scanner(
            id="scn_stale_gps",
            display_name="Stale GPS Scanner",
            hardware_id="stale-gps-001",
            token_hash="hash",
            status="online",
            latitude=-6.226100,
            longitude=106.852900,
            location_source="browser_geolocation",
            location_observed_at=observed_at - timedelta(minutes=2),
            location_accuracy_m=18.0,
            enabled=True,
        )
        db.add(scanner)
        db.flush()
        db.add(ScannerConfiguration(scanner_id=scanner.id))
        db.commit()

        process_batch(
            db,
            scanner,
            ObservationBatchIn(
                batch_id="batch-stale-gps",
                sent_at=observed_at,
                time_source="usb_host_synchronized",
                boot_id="boot-stale-gps",
                batch_sequence=1,
                clock_sync_age_ms=10,
                observations=[
                    {
                        "observation_id": "obs-stale-gps",
                        "observed_at": observed_at,
                        "time_source": "usb_host_synchronized",
                        "boot_id": "boot-stale-gps",
                        "monotonic_ms": 1_000,
                        "clock_sync_age_ms": 10,
                        "address": "80:e1:26:9e:3e:e3",
                        "address_type": "public",
                        "rssi": -66,
                    }
                ],
            ),
        )

        device = db.execute(select(LogicalDevice)).scalar_one()
        estimate = db.execute(select(DeviceLocationEstimate)).scalar_one()
        assert device.latitude == pytest.approx(-6.226100)
        assert device.longitude == pytest.approx(106.852900)
        assert estimate.details["scanner_position_available"] is True
        assert estimate.details["scanner_position_current"] is False
