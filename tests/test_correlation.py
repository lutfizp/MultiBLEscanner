from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from backend.app.correlation import (
    AkiyamaPairCost,
    akiyama_pair_cost,
    assign_akiyama_pairs,
    extract_approved_tokens,
    parse_token_rules,
    rssi_regression_difference,
)
from backend.app.database import Base
from backend.app.config import Settings
from backend.app.device_intelligence import analyze_manufacturer_data
from backend.app.models import (
    DeviceEnrichment,
    DeviceEvent,
    DeviceIdentityCorrelation,
    DeviceLocationEstimate,
    DeviceTrackingSession,
    LogicalDevice,
    ManualDeviceCorrelationDecision,
    Observation,
    ObservedIdentity,
    Scanner,
    ScannerConfiguration,
    SystemSetting,
)
from backend.app.processing import ProcessingSettings, infer_proximity_from_rssi, utcnow
from backend.app.schemas import DevicePatchIn, ManualCorrelationIn, ObservationBatchIn
from backend.app.services import (
    apply_manual_correlation,
    device_detail,
    patch_device_metadata,
    process_batch,
    run_identity_correlation,
)


def test_akiyama_cost_uses_time_and_linear_regression_rssi_residual():
    start = utcnow()
    predecessor_points = [
        (start, -80),
        (start + timedelta(seconds=1), -79),
        (start + timedelta(seconds=2), -78),
    ]
    successor_points = [
        (start + timedelta(seconds=5), -74),
        (start + timedelta(seconds=6), -73),
        (start + timedelta(seconds=7), -72),
    ]

    difference = rssi_regression_difference(predecessor_points, successor_points)

    assert difference is not None
    residual, predecessor_samples, successor_samples = difference
    assert residual == pytest.approx(1.0)
    pair = akiyama_pair_cost(
        predecessor_points[-1][0],
        successor_points[0][0],
        residual,
        alpha=0.14,
        search_window_seconds=6,
        predecessor_sample_count=predecessor_samples,
        successor_sample_count=successor_samples,
    )
    assert pair is not None
    assert pair.time_difference_seconds == pytest.approx(3.0)
    assert pair.cost == pytest.approx((3.0**2 + (0.14 * 1.0) ** 2) ** 0.5)


def test_akiyama_assignment_can_leave_an_unsafe_pair_unmatched():
    pair = AkiyamaPairCost(
        time_difference_seconds=10,
        rssi_difference_db=30,
        alpha=1.0,
        cost=60,
        predecessor_sample_count=3,
        successor_sample_count=3,
    )

    links = assign_akiyama_pairs(["old"], ["new"], {("old", "new"): pair}, unmatched_cost=30)

    assert links == []


def test_only_explicit_five_byte_scoped_ad_tokens_are_extractable():
    rules = parse_token_rules(
        [
            {
                "rule_id": "apple-static-test-token",
                "ad_type": "0xff",
                "company_id": "0x004c",
                "offset_bytes": 2,
                "length_bytes": 5,
            },
            {
                "rule_id": "too-short",
                "ad_type": "0xff",
                "company_id": "0x004c",
                "offset_bytes": 2,
                "length_bytes": 4,
            },
            {
                "rule_id": "unscoped",
                "ad_type": "0xff",
                "offset_bytes": 2,
                "length_bytes": 5,
            },
        ]
    )
    tokens = extract_approved_tokens(
        {"structures": [{"type": "0xff", "company_id": "0x004c", "data": "4c00aabbccddee"}]},
        rules,
    )

    assert len(rules) == 1
    assert len(tokens) == 1
    token = next(iter(tokens.values()))
    assert token["rule_id"] == "apple-static-test-token"
    assert token["bit_length"] == 40


def _test_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _scanner(db, scanner_id: str, zone: str, latitude: float, longitude: float) -> Scanner:
    scanner = Scanner(
        id=scanner_id,
        display_name=scanner_id,
        hardware_id=f"hardware-{scanner_id}",
        token_hash="hash",
        enabled=True,
        zone=zone,
        latitude=latitude,
        longitude=longitude,
    )
    db.add(scanner)
    db.flush()
    db.add(ScannerConfiguration(scanner_id=scanner.id))
    return scanner


def _trusted_token_batch(batch_id: str, observation_id: str, observed_at, address: str) -> ObservationBatchIn:
    raw_advertising = "08ff4c00aabbccddee"
    return ObservationBatchIn(
        batch_id=batch_id,
        sent_at=observed_at,
        time_source="usb_host_synchronized",
        boot_id="test-boot",
        clock_sync_age_ms=1,
        observations=[
            {
                "observation_id": observation_id,
                "observed_at": observed_at,
                "time_source": "usb_host_synchronized",
                "boot_id": "test-boot",
                "monotonic_ms": int(observed_at.timestamp() * 1000),
                "scan_cycle": 1,
                "clock_sync_age_ms": 1,
                "address": address,
                "address_type": "random",
                "rssi": -70,
                "raw_advertising_payload": raw_advertising,
                "advertising_packet_length": 9,
                "packet_length": 9,
                "payload_layout_version": 2,
            }
        ],
    )


def _trusted_apple_nearby_batch(
    batch_id: str,
    observation_id: str,
    observed_at,
    address: str,
    *,
    auth_tag: str = "aabbcc",
    rssi: int = -70,
) -> ObservationBatchIn:
    manufacturer_data = f"4c0010050102{auth_tag}"
    raw_advertising = f"{(len(manufacturer_data) // 2) + 1:02x}ff{manufacturer_data}"
    return ObservationBatchIn(
        batch_id=batch_id,
        sent_at=observed_at,
        time_source="usb_host_synchronized",
        boot_id="test-boot",
        clock_sync_age_ms=1,
        observations=[
            {
                "observation_id": observation_id,
                "observed_at": observed_at,
                "time_source": "usb_host_synchronized",
                "boot_id": "test-boot",
                "monotonic_ms": int(observed_at.timestamp() * 1000),
                "scan_cycle": 1,
                "clock_sync_age_ms": 1,
                "address": address,
                "address_type": "random",
                "rssi": rssi,
                "raw_advertising_payload": raw_advertising,
                "advertising_packet_length": len(raw_advertising) // 2,
                "packet_length": len(raw_advertising) // 2,
                "payload_layout_version": 2,
            }
        ],
    )


def test_apple_continuity_parser_handles_concatenated_published_tlvs():
    manufacturer_data = (
        "4c00"
        "10050102aabbcc"
        "0c0e010001aa00112233445566778899"
    )

    profile = analyze_manufacturer_data(manufacturer_data)

    assert profile["continuity_subtypes"] == ["handoff", "nearby_info"]
    nearby, handoff = profile["continuity_messages"]
    assert nearby["authentication_tag_hash"]
    assert nearby["activity_level"] == 1
    assert handoff["iv"] == 1
    assert handoff["encrypted_payload_hash"]
    assert "00112233445566778899" not in str(profile)


def test_live_apple_address_transition_creates_proposal_without_merging():
    TestSession = _test_session()
    start = utcnow() - timedelta(seconds=10)
    with TestSession() as db:
        scanner = _scanner(db, "scn_apple", "Lab", -6.2, 106.8)
        db.commit()
        process_batch(
            db,
            scanner,
            _trusted_apple_nearby_batch(
                "apple-old-1",
                "apple-old-observation-1",
                start,
                "c1:7d:8a:b9:7f:4e",
            ),
        )
        process_batch(
            db,
            scanner,
            _trusted_apple_nearby_batch(
                "apple-old-2",
                "apple-old-observation-2",
                start + timedelta(seconds=1),
                "c1:7d:8a:b9:7f:4e",
                rssi=-69,
            ),
        )

        result = process_batch(
            db,
            scanner,
            _trusted_apple_nearby_batch(
                "apple-new-1",
                "apple-new-observation-1",
                start + timedelta(seconds=3),
                "5c:c5:ba:61:40:54",
                rssi=-68,
            ),
        )

        assert result["identity_correlations"] == {"accepted": 0, "proposals": 1}
        correlation = db.execute(select(DeviceIdentityCorrelation)).scalar_one()
        assert correlation.method == "apple_continuity_transition_v1"
        assert correlation.status == "proposal"
        assert correlation.details["subtype_overlap"] == ["nearby_info"]
        assert correlation.details["matching_transition_tag_hashes"]
        assert correlation.details["automatic_acceptance"] is False
        assert correlation.details["identity_claim"] == "possible_match_not_confirmed_physical_identity"
        assert db.execute(
            select(func.count(LogicalDevice.id)).where(LogicalDevice.ignored.is_(False))
        ).scalar_one() == 2


def test_approved_ad_token_moves_canonical_device_location_between_scanners():
    TestSession = _test_session()
    start = utcnow() - timedelta(seconds=20)
    with TestSession() as db:
        tebet = _scanner(db, "scn_tebet", "Tebet", -6.2297, 106.8402)
        bekasi = _scanner(db, "scn_bekasi", "Bekasi", -6.2383, 106.9756)
        db.add(
            SystemSetting(
                key="correlation_token_rules",
                value=[
                    {
                        "rule_id": "approved-company-token",
                        "ad_type": "0xff",
                        "company_id": "0x004c",
                        "offset_bytes": 2,
                        "length_bytes": 5,
                    }
                ],
            )
        )
        db.commit()

        process_batch(db, tebet, _trusted_token_batch("old-1", "old-obs-1", start, "c1:7d:8a:b9:7f:4e"))
        process_batch(
            db,
            tebet,
            _trusted_token_batch("old-2", "old-obs-2", start + timedelta(seconds=1), "c1:7d:8a:b9:7f:4e"),
        )
        first_result = process_batch(
            db,
            bekasi,
            _trusted_token_batch("new-1", "new-obs-1", start + timedelta(seconds=5), "5c:c5:ba:61:40:54"),
        )
        assert first_result["identity_correlations"]["accepted"] == 0
        result = process_batch(
            db,
            bekasi,
            _trusted_token_batch("new-2", "new-obs-2", start + timedelta(seconds=6), "5c:c5:ba:61:40:54"),
        )

        assert result["identity_correlations"]["accepted"] == 1
        canonical = db.execute(select(LogicalDevice).where(LogicalDevice.ignored.is_(False))).scalar_one()
        assert canonical.current_scanner_id == "scn_bekasi"
        assert canonical.current_zone == "Bekasi"
        assert canonical.movement_status == "relocated_between_scanners"
        assert db.execute(select(func.count(Observation.id)).where(Observation.logical_device_id == canonical.id)).scalar_one() == 4
        correlation = db.execute(select(DeviceIdentityCorrelation)).scalar_one()
        assert correlation.status == "accepted"
        assert correlation.method == "approved_ad_token_carryover"
        assert correlation.details["token_bit_length"] == 40
        assert db.execute(select(func.count(DeviceLocationEstimate.id)).where(DeviceLocationEstimate.logical_device_id == canonical.id)).scalar_one() == 2
        detail = device_detail(db, canonical.id)
        assert detail is not None
        assert len(detail["identity_correlations"]) == 1


def test_akiyama_assignment_is_recorded_as_proposal_without_merging_devices():
    TestSession = _test_session()
    start = utcnow() - timedelta(seconds=30)
    with TestSession() as db:
        scanner = _scanner(db, "scn_assignment", "Lab", -6.2, 106.8)
        predecessor = ObservedIdentity(
            address="cf:28:3b:81:15:b3",
            address_type="random",
            randomized_address=True,
            first_seen_at=start,
            last_seen_at=start + timedelta(seconds=2),
        )
        successor = ObservedIdentity(
            address="57:38:f6:70:67:6a",
            address_type="random",
            randomized_address=True,
            first_seen_at=start + timedelta(seconds=4),
            last_seen_at=start + timedelta(seconds=6),
        )
        predecessor_device = LogicalDevice(
            primary_address=predecessor.address,
            primary_address_type="random",
            display_name="old",
            status="active",
            current_scanner_id=scanner.id,
            first_seen_at=start,
            last_seen_at=start + timedelta(seconds=2),
        )
        successor_device = LogicalDevice(
            primary_address=successor.address,
            primary_address_type="random",
            display_name="new",
            status="active",
            current_scanner_id=scanner.id,
            first_seen_at=start + timedelta(seconds=4),
            last_seen_at=start + timedelta(seconds=6),
        )
        db.add_all([predecessor, successor, predecessor_device, successor_device])
        db.flush()
        for index, rssi in enumerate([-80, -79, -78]):
            db.add(
                Observation(
                    scanner_id=scanner.id,
                    batch_id="old",
                    observation_id=f"old-{index}",
                    observed_identity_id=predecessor.id,
                    logical_device_id=predecessor_device.id,
                    observed_at=start + timedelta(seconds=index),
                    server_received_at=start + timedelta(seconds=index),
                    processed_at=start + timedelta(seconds=index),
                    rssi=rssi,
                    processing_notes={"time_provenance": {"time_quality": "trusted"}},
                )
            )
        for index, rssi in enumerate([-75, -74, -73]):
            observed_at = start + timedelta(seconds=4 + index)
            db.add(
                Observation(
                    scanner_id=scanner.id,
                    batch_id="new",
                    observation_id=f"new-{index}",
                    observed_identity_id=successor.id,
                    logical_device_id=successor_device.id,
                    observed_at=observed_at,
                    server_received_at=observed_at,
                    processed_at=observed_at,
                    rssi=rssi,
                    processing_notes={"time_provenance": {"time_quality": "trusted"}},
                )
            )
        db.commit()

        result = run_identity_correlation(db)
        db.commit()

        assert result == {"accepted": 0, "proposals": 1}
        correlation = db.execute(select(DeviceIdentityCorrelation)).scalar_one()
        assert correlation.method == "akiyama_time_rssi_linear_assignment_v1"
        assert correlation.status == "proposal"
        assert correlation.rssi_difference_db == pytest.approx(1.0)
        assert db.execute(select(func.count(LogicalDevice.id)).where(LogicalDevice.ignored.is_(False))).scalar_one() == 2


def test_live_batch_skips_unconfigured_statistical_correlation_review():
    TestSession = _test_session()
    with TestSession() as db:
        scanner = _scanner(db, "scn_live_ingest", "Lab", -6.2, 106.8)
        db.commit()
        result = process_batch(
            db,
            scanner,
            _trusted_token_batch("live-ingest", "live-observation", utcnow(), "cf:28:3b:81:15:b3"),
        )

        assert result["accepted"] == 1
        assert result["identity_correlations"] == {"accepted": 0, "proposals": 0}


def test_live_distance_model_is_explicitly_journal_based():
    result = infer_proximity_from_rssi(-70)

    assert result.method == "journal_esp32_log_distance_baseline_v1"
    assert result.band == "signal_moderate"
    assert result.distance_m == pytest.approx(14.13, rel=1e-3)
    assert result.distance_range_m is None


def test_manual_merge_moves_all_device_owned_records_and_preserves_metadata():
    TestSession = _test_session()
    now = utcnow()
    with TestSession() as db:
        scanner = _scanner(db, "scn_manual_merge", "Lab", -6.2, 106.8)
        target_identity = ObservedIdentity(
            id="identity-merge-target",
            address="10:20:30:40:50:70",
            address_type="public",
            first_seen_at=now - timedelta(seconds=10),
            last_seen_at=now - timedelta(seconds=10),
        )
        source_identity = ObservedIdentity(
            id="identity-merge-source",
            address="10:20:30:40:50:71",
            address_type="public",
            first_seen_at=now,
            last_seen_at=now,
        )
        target = LogicalDevice(
            id="device-merge-target",
            alias="Canonical headset",
            primary_address=target_identity.address,
            primary_address_type="public",
            display_name=target_identity.address,
            status="active",
            movement_status="signal_stable",
            current_scanner_id=scanner.id,
            first_seen_at=now - timedelta(seconds=10),
            last_seen_at=now - timedelta(seconds=10),
            observation_count=1,
            notes="Target note",
            tags=["target"],
        )
        source = LogicalDevice(
            id="device-merge-source",
            primary_address=source_identity.address,
            primary_address_type="public",
            display_name="Named source",
            status="active",
            movement_status="probably_moving",
            current_scanner_id=scanner.id,
            current_zone="Lab",
            latitude=-6.2,
            longitude=106.8,
            first_seen_at=now,
            last_seen_at=now,
            observation_count=1,
            known=True,
            notes="Source note",
            tags=["source", "target"],
        )
        db.add_all([target_identity, source_identity, target, source])
        db.flush()
        db.add_all(
            [
                Observation(
                    scanner_id=scanner.id,
                    batch_id="merge-target",
                    observation_id="merge-target-observation",
                    observed_identity_id=target_identity.id,
                    logical_device_id=target.id,
                    observed_at=now - timedelta(seconds=10),
                    server_received_at=now - timedelta(seconds=10),
                    processed_at=now - timedelta(seconds=10),
                    rssi=-70,
                ),
                Observation(
                    scanner_id=scanner.id,
                    batch_id="merge-source",
                    observation_id="merge-source-observation",
                    observed_identity_id=source_identity.id,
                    logical_device_id=source.id,
                    observed_at=now,
                    server_received_at=now,
                    processed_at=now,
                    rssi=-60,
                ),
                DeviceLocationEstimate(
                    logical_device_id=source.id,
                    scanner_id=scanner.id,
                    estimated_at=now,
                ),
                DeviceEvent(
                    event_type="device_signal_band_changed",
                    logical_device_id=source.id,
                    observed_identity_id=source_identity.id,
                    occurred_at=now,
                ),
                DeviceEnrichment(
                    logical_device_id=source.id,
                    observed_identity_id=source_identity.id,
                    scanner_id=scanner.id,
                    source_observation_id="merge-source-observation",
                    enriched_at=now,
                    status="success",
                ),
                DeviceTrackingSession(
                    logical_device_id=source.id,
                    state="live",
                    started_at=now,
                    last_lease_at=now,
                    expires_at=now + timedelta(minutes=1),
                ),
            ]
        )
        db.commit()

        result = apply_manual_correlation(
            db,
            ManualCorrelationIn(
                source_logical_device_id=source.id,
                target_logical_device_id=target.id,
                action="merge",
                reason="operator confirmed same headset",
            ),
            Settings(),
        )

        db.refresh(target)
        db.refresh(source)
        assert result["canonical_device_id"] == target.id
        assert target.observation_count == 2
        assert target.known is True
        assert target.notes == "Target note\n\nSource note"
        assert target.tags == ["target", "source"]
        assert source.status == "merged"
        assert source.current_scanner_id is None
        for model in (
            Observation,
            DeviceLocationEstimate,
            DeviceEvent,
            DeviceEnrichment,
            DeviceTrackingSession,
        ):
            assert db.execute(
                select(func.count(model.id)).where(model.logical_device_id == source.id)
            ).scalar_one() == 0


def test_manual_split_rebuilds_each_device_from_attributable_identity_evidence():
    TestSession = _test_session()
    now = utcnow()
    with TestSession() as db:
        scanner = _scanner(db, "scn_manual_split", "Bekasi", -6.2383, 106.9756)
        first_identity = ObservedIdentity(
            id="identity-split-first",
            address="10:20:30:40:50:72",
            address_type="public",
            local_name="First device",
            first_seen_at=now - timedelta(seconds=10),
            last_seen_at=now - timedelta(seconds=10),
        )
        second_identity = ObservedIdentity(
            id="identity-split-second",
            address="10:20:30:40:50:73",
            address_type="public",
            local_name="Second device",
            first_seen_at=now,
            last_seen_at=now,
        )
        source = LogicalDevice(
            id="device-split-source",
            primary_address=second_identity.address,
            primary_address_type="public",
            display_name="Merged record",
            status="active",
            movement_status="signal_stable",
            current_scanner_id=scanner.id,
            current_zone="Bekasi",
            latitude=-6.2383,
            longitude=106.9756,
            first_seen_at=now - timedelta(seconds=10),
            last_seen_at=now,
            observation_count=2,
        )
        db.add_all([first_identity, second_identity, source])
        db.flush()
        first_time = now - timedelta(seconds=10)
        for identity, observed_at, suffix, rssi in (
            (first_identity, first_time, "first", -75),
            (second_identity, now, "second", -60),
        ):
            db.add(
                Observation(
                    scanner_id=scanner.id,
                    batch_id=f"split-{suffix}",
                    observation_id=f"split-observation-{suffix}",
                    observed_identity_id=identity.id,
                    logical_device_id=source.id,
                    observed_at=observed_at,
                    server_received_at=observed_at,
                    processed_at=observed_at,
                    rssi=rssi,
                    estimated_distance_m=2.0,
                    processing_notes={
                        "proximity_model": {
                            "band": "signal_strong" if suffix == "second" else "signal_moderate",
                            "smoothed_rssi": float(rssi),
                        },
                        "movement_evidence": {"applied_status": "signal_stable"},
                        "anchor_snapshot": {
                            "zone": "Bekasi",
                            "latitude": -6.2383,
                            "longitude": 106.9756,
                            "anchored_at": observed_at.isoformat().replace("+00:00", "Z"),
                        },
                    },
                )
            )
            db.add(
                DeviceLocationEstimate(
                    logical_device_id=source.id,
                    scanner_id=scanner.id,
                    estimated_at=observed_at,
                    zone="Bekasi",
                    proximity_band="signal_strong" if suffix == "second" else "signal_moderate",
                )
            )
        db.add_all(
            [
                DeviceEnrichment(
                    logical_device_id=source.id,
                    observed_identity_id=second_identity.id,
                    scanner_id=scanner.id,
                    source_observation_id="split-observation-second",
                    enriched_at=now,
                    status="success",
                ),
                DeviceEvent(
                    event_type="device_signal_band_changed",
                    logical_device_id=source.id,
                    observed_identity_id=second_identity.id,
                    occurred_at=now,
                ),
            ]
        )
        db.commit()

        result = apply_manual_correlation(
            db,
            ManualCorrelationIn(
                source_logical_device_id=source.id,
                observed_identity_id=second_identity.id,
                action="split",
                reason="operator found an incorrect merge",
            ),
            Settings(),
        )

        split_device = db.get(LogicalDevice, result["split_device_id"])
        db.refresh(source)
        assert split_device is not None
        assert source.primary_address == first_identity.address
        assert source.observation_count == 1
        assert split_device.primary_address == second_identity.address
        assert split_device.display_name == "Second device"
        assert split_device.current_scanner_id == scanner.id
        assert split_device.latitude == pytest.approx(-6.2383)
        assert split_device.longitude == pytest.approx(106.9756)
        assert db.execute(
            select(func.count(Observation.id)).where(Observation.logical_device_id == split_device.id)
        ).scalar_one() == 1
        assert db.execute(
            select(func.count(DeviceLocationEstimate.id)).where(
                DeviceLocationEstimate.logical_device_id == split_device.id
            )
        ).scalar_one() == 1
        assert db.execute(
            select(func.count(DeviceEnrichment.id)).where(
                DeviceEnrichment.logical_device_id == split_device.id
            )
        ).scalar_one() == 1


@pytest.mark.parametrize(
    ("action", "expected_status", "expected_active_devices"),
    [
        ("accept_proposal", "accepted", 1),
        ("reject_proposal", "rejected", 2),
    ],
)
def test_operator_review_resolves_identity_proposal_without_statistical_auto_accept(
    action,
    expected_status,
    expected_active_devices,
):
    TestSession = _test_session()
    start = utcnow() - timedelta(seconds=10)
    with TestSession() as db:
        scanner = _scanner(db, f"scn_review_{action}", "Lab", -6.2, 106.8)
        db.commit()
        process_batch(
            db,
            scanner,
            _trusted_apple_nearby_batch(
                f"{action}-old-1",
                f"{action}-old-observation-1",
                start,
                "c1:7d:8a:b9:7f:4e",
            ),
        )
        process_batch(
            db,
            scanner,
            _trusted_apple_nearby_batch(
                f"{action}-old-2",
                f"{action}-old-observation-2",
                start + timedelta(seconds=1),
                "c1:7d:8a:b9:7f:4e",
                rssi=-69,
            ),
        )
        process_batch(
            db,
            scanner,
            _trusted_apple_nearby_batch(
                f"{action}-new-1",
                f"{action}-new-observation-1",
                start + timedelta(seconds=3),
                "5c:c5:ba:61:40:54",
                rssi=-68,
            ),
        )
        correlation = db.execute(select(DeviceIdentityCorrelation)).scalar_one()

        result = apply_manual_correlation(
            db,
            ManualCorrelationIn(
                source_logical_device_id=correlation.successor_logical_device_id,
                correlation_id=correlation.id,
                action=action,
                reason="operator review",
            ),
            Settings(),
        )

        db.refresh(correlation)
        assert result["correlation_status"] == expected_status
        assert correlation.status == expected_status
        assert db.execute(
            select(func.count(LogicalDevice.id)).where(LogicalDevice.ignored.is_(False))
        ).scalar_one() == expected_active_devices
        decision = db.execute(select(ManualDeviceCorrelationDecision)).scalar_one()
        assert decision.correlation_id == correlation.id


def test_device_metadata_patch_is_validated_and_audited():
    TestSession = _test_session()
    now = utcnow()
    with TestSession() as db:
        device = LogicalDevice(
            primary_address="10:20:30:40:50:74",
            primary_address_type="public",
            display_name="Observed device",
            status="active",
            movement_status="stationary",
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(device)
        db.commit()

        result = patch_device_metadata(
            db,
            device.id,
            DevicePatchIn(
                alias="  Meeting headset  ",
                notes="  Assigned to meeting room  ",
                tags=["audio", "audio", "meeting"],
                known=True,
            ),
        )

        assert result is not None
        assert result["alias"] == "Meeting headset"
        assert result["notes"] == "Assigned to meeting room"
        assert result["tags"] == ["audio", "meeting"]
        assert result["known"] is True
        event = db.execute(
            select(DeviceEvent).where(DeviceEvent.event_type == "manual_device_metadata_updated")
        ).scalar_one()
        assert event.details["updated_fields"] == ["alias", "known", "notes", "tags"]
