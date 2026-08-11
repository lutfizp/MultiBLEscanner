from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from math import asin, cos, radians, sin, sqrt
from statistics import median
from typing import Any

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .bluetooth_ad import parse_advertising_and_scan_response
from .config import Settings
from .device_intelligence import (
    analyze_manufacturer_data,
    classify_flipper_zero,
    infer_device_category,
)
from .device_records import merge_logical_devices, split_logical_identity
from .correlation import (
    akiyama_pair_cost,
    alpha_from_p90_overlap,
    assign_akiyama_pairs,
    extract_approved_tokens,
    parse_token_rules,
    rssi_regression_difference,
)
from .models import (
    DeviceEnrichment,
    DeviceEvent,
    DeviceIdentityCorrelation,
    DeviceLocationEstimate,
    DeviceTrackingSample,
    DeviceTrackingSession,
    LogicalDevice,
    ManualDeviceCorrelationDecision,
    Observation,
    ObservedIdentity,
    ProcessingError,
    Scanner,
    ScannerConfiguration,
    ScannerHeartbeat,
    SystemSetting,
)
from .processing import (
    JOURNAL_PATH_LOSS_EXPONENT,
    JOURNAL_REFERENCE_RSSI_DBM,
    JOURNAL_VALIDATED_DISTANCE_M,
    MovementResult,
    ProcessingSettings,
    RSSI_MOVEMENT_DWELL_OBSERVATIONS,
    RSSIWindowMetrics,
    SIGNAL_BAND_HYSTERESIS_DB,
    ensure_utc,
    evaluate_presence_status,
    infer_proximity_from_rssi,
    is_randomized_address,
    is_synthetic_address_pattern,
    movement_result_from_metrics,
    normalize_address,
    observed_again_status,
    proximity_band,
    rssi_window_metrics,
    signal_band_from_rssi,
    signal_band_with_hysteresis,
    utcnow,
)
from .schemas import (
    BLEObservationIn,
    DevicePatchIn,
    GATTEnrichmentReportIn,
    HeartbeatIn,
    ManualCorrelationIn,
    ObservationBatchIn,
    ScannerPatchIn,
    ScannerPositionIn,
    ScannerRegistrationIn,
    SettingsPatchIn,
)
from .security import generate_scanner_token, hash_scanner_token, verify_scanner_token


DEFAULT_SETTINGS: dict[str, tuple[Any, str]] = {
    "correlation_rotation_window_seconds": (
        20,
        "Maximum address handover interval for RSSI-time assignment. Calibrate this to scanner cadence; it is not an identity guarantee.",
    ),
    "correlation_evaluation_window_seconds": (
        20,
        "RSSI interval after a new address begins. At least the configured number of trusted samples is required for a statistical proposal.",
    ),
    "correlation_min_regression_samples": (
        3,
        "Minimum trusted observations in both RSSI regression windows. Fewer samples produce no statistical proposal.",
    ),
    "correlation_alpha": (
        None,
        "Optional calibrated Akiyama RSSI-time scale. When empty, the service records a per-run 90th-percentile width match; it does not auto-merge devices.",
    ),
    "correlation_unmatched_cost_seconds": (
        30.0,
        "Cost of leaving an address unmatched in the global RSSI-time assignment. This prevents forced candidate links.",
    ),
    "correlation_token_carryover_max_seconds": (
        3600,
        "Maximum interval for an approved static AD token to carry a logical device across a random-address change.",
    ),
    "correlation_token_min_observations": (
        2,
        "Minimum raw observations that must contain an approved token before it may carry an identity across an address change.",
    ),
    "correlation_token_rules": (
        [],
        "Operator-approved protocol token rules only. Each rule needs rule_id, ad_type, offset_bytes, length_bytes (at least 5 bytes), plus company_id or service_uuid scope.",
    ),
}

SERVER_SETTING_KEYS = {
    "presence_missing_seconds",
    "presence_offline_seconds",
    "heartbeat_timeout_seconds",
    "raw_observation_retention_days",
    "summary_retention_days",
}
DEPRECATED_CORRELATION_SETTING_KEYS = {
    "correlation_auto_accept_statistical",
    "correlation_statistical_max_cost",
}

BROWSER_SCANNER_POSITION_MAX_AGE_SECONDS = 30
LOCATION_EVENT_MIN_DISTANCE_M = 25.0


def coordinate_distance_m(
    first_latitude: float | None,
    first_longitude: float | None,
    second_latitude: float | None,
    second_longitude: float | None,
) -> float | None:
    if None in (first_latitude, first_longitude, second_latitude, second_longitude):
        return None
    first_lat = radians(float(first_latitude))
    second_lat = radians(float(second_latitude))
    delta_lat = second_lat - first_lat
    delta_lon = radians(float(second_longitude) - float(first_longitude))
    value = (
        sin(delta_lat / 2) ** 2
        + cos(first_lat) * cos(second_lat) * sin(delta_lon / 2) ** 2
    )
    return 6_371_000.0 * 2 * asin(sqrt(min(1.0, value)))


def scanner_position_is_current(scanner: Scanner, observed_at: datetime) -> bool:
    if scanner.latitude is None or scanner.longitude is None:
        return False
    if scanner.location_source != "browser_geolocation":
        return True
    if scanner.location_observed_at is None:
        return False
    age_seconds = abs(
        (ensure_utc(observed_at) - ensure_utc(scanner.location_observed_at)).total_seconds()
    )
    return age_seconds <= BROWSER_SCANNER_POSITION_MAX_AGE_SECONDS


def scanner_position_is_available(scanner: Scanner) -> bool:
    return scanner.latitude is not None and scanner.longitude is not None


TRUSTED_CLOCK_SYNC_MAX_AGE_MS = 5 * 60 * 1000
MAX_FUTURE_OBSERVATION_SKEW = timedelta(minutes=5)
LEGACY_SIGNAL_SETTING_KEYS = {"movement_rssi_delta", "rssi_smoothing_alpha"}


def make_scanner_id() -> str:
    return f"scn_{secrets.token_hex(6)}"


def latest_datetime(first: datetime | None, second: datetime) -> datetime:
    if first is None:
        return ensure_utc(second)
    normalized_first = ensure_utc(first)
    normalized_second = ensure_utc(second)
    return normalized_first if normalized_first >= normalized_second else normalized_second


def rssi_samples_for_window_metric(
    db: Session,
    logical_device_id: str,
    current_scanner_id: str,
    current_rssi: int,
) -> dict[str, list[float]]:
    """Return chronological RSSI samples grouped by scanner for the paper metric."""
    rows = db.execute(
        select(Observation.scanner_id, Observation.rssi, Observation.observed_at, Observation.id)
        .where(Observation.logical_device_id == logical_device_id)
        .order_by(desc(Observation.observed_at), desc(Observation.id))
        .limit(200)
    ).all()
    grouped: dict[str, list[float]] = {}
    for scanner_id, rssi, _observed_at, _observation_id in reversed(rows):
        grouped.setdefault(scanner_id, []).append(float(rssi))
    grouped.setdefault(current_scanner_id, []).append(float(current_rssi))
    return grouped


def scanner_config_payload(
    config: ScannerConfiguration,
    tracking_focus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "version": config.version,
        "scan_interval_ms": config.scan_interval_ms,
        "upload_interval_seconds": config.upload_interval_seconds,
        "batch_size": config.batch_size,
        "rssi_min": config.rssi_min,
        "tracking_focus": tracking_focus,
    }


def proximity_model_payload(
    proximity: Any,
    raw_rssi: int | float,
    smoothed_rssi: float,
    metrics: RSSIWindowMetrics,
) -> dict[str, Any]:
    return {
        "method": proximity.method,
        "sequence_method": metrics.method,
        "band": proximity.band,
        "estimated_distance_m": proximity.distance_m,
        "distance_range_m": list(proximity.distance_range_m) if proximity.distance_range_m else None,
        "confidence": proximity.confidence,
        "band_probabilities": proximity.probabilities,
        "raw_rssi": raw_rssi,
        "smoothed_rssi": smoothed_rssi,
        "distance_available": proximity.distance_m is not None,
        "distance_model": "log_distance_path_loss",
        "distance_model_reference_rssi_dbm": JOURNAL_REFERENCE_RSSI_DBM,
        "distance_model_path_loss_exponent": JOURNAL_PATH_LOSS_EXPONENT,
        "distance_model_validated_distance_m": JOURNAL_VALIDATED_DISTANCE_M,
        "distance_model_status": (
            "within_published_esp32_baseline_range"
            if proximity.distance_m is not None and proximity.distance_m <= JOURNAL_VALIDATED_DISTANCE_M
            else "outside_published_baseline_range"
            if proximity.distance_m is not None
            else "unavailable"
        ),
        "confidence_basis": "paper_rssi_window_reliability" if metrics.window_ready else "rssi_window_not_ready",
        "window_size": metrics.window_size,
        "window_ready": metrics.window_ready,
        "observed_anchor_count": metrics.observed_anchor_count,
        "anchor_count": metrics.anchor_count,
        "current_window_means": metrics.current_window_means,
        "previous_window_means": metrics.previous_window_means,
        "absolute_changes_db": metrics.absolute_changes_db,
        "anchor_weights": metrics.weights,
        "weighted_mean_change_db": metrics.weighted_mean_change_db,
        "rssi_metric": metrics.rssi_metric,
        "signal_reliability": metrics.reliability,
        "movement_threshold": metrics.movement_threshold,
    }


def ensure_default_settings(db: Session) -> None:
    changed = False
    for key in (
        LEGACY_SIGNAL_SETTING_KEYS
        | SERVER_SETTING_KEYS
        | DEPRECATED_CORRELATION_SETTING_KEYS
    ):
        legacy = db.get(SystemSetting, key)
        if legacy is not None:
            db.delete(legacy)
            changed = True
    for key, (value, description) in DEFAULT_SETTINGS.items():
        existing = db.get(SystemSetting, key)
        if existing is None:
            db.add(SystemSetting(key=key, value=value, description=description))
            changed = True
    if changed:
        db.commit()


def register_scanner(db: Session, payload: ScannerRegistrationIn, settings: Settings) -> tuple[Scanner, str, ScannerConfiguration]:
    existing = db.execute(select(Scanner).where(Scanner.hardware_id == payload.hardware_id)).scalar_one_or_none()
    if existing is not None:
        token = generate_scanner_token()
        existing.token_hash = hash_scanner_token(token, settings.scanner_token_salt)
        existing.status = "registered"
        existing.enabled = True
        existing.firmware_version = payload.firmware_version or existing.firmware_version
        existing.hardware_version = payload.hardware_version or existing.hardware_version
        config = db.execute(select(ScannerConfiguration).where(ScannerConfiguration.scanner_id == existing.id)).scalar_one()
        db.commit()
        return existing, token, config

    token = generate_scanner_token()
    scanner = Scanner(
        id=make_scanner_id(),
        display_name=payload.display_name or payload.hardware_id,
        hardware_id=payload.hardware_id,
        token_hash=hash_scanner_token(token, settings.scanner_token_salt),
        installation_name=payload.installation_name,
        firmware_version=payload.firmware_version,
        hardware_version=payload.hardware_version,
        status="registered",
    )
    db.add(scanner)
    db.flush()

    config = ScannerConfiguration(scanner_id=scanner.id)
    db.add(config)
    db.commit()
    db.refresh(scanner)
    db.refresh(config)
    return scanner, token, config


def authenticate_scanner(db: Session, scanner_id: str, token: str, settings: Settings) -> Scanner | None:
    scanner = db.get(Scanner, scanner_id)
    if scanner is None or not scanner.enabled:
        return None
    if not verify_scanner_token(token, scanner.token_hash, settings.scanner_token_salt):
        return None
    return scanner


def get_scanner_config(db: Session, scanner_id: str) -> ScannerConfiguration:
    config = db.execute(select(ScannerConfiguration).where(ScannerConfiguration.scanner_id == scanner_id)).scalar_one_or_none()
    if config is None:
        config = ScannerConfiguration(scanner_id=scanner_id)
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def record_heartbeat(db: Session, scanner: Scanner, payload: HeartbeatIn) -> dict[str, Any]:
    now = utcnow()
    existing = db.execute(
        select(ScannerHeartbeat).where(
            ScannerHeartbeat.scanner_id == scanner.id,
            ScannerHeartbeat.message_id == payload.message_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {"accepted": False, "duplicate": True}

    heartbeat = ScannerHeartbeat(
        scanner_id=scanner.id,
        message_id=payload.message_id,
        scanner_time=ensure_utc(payload.scanner_time, now) if payload.scanner_time else None,
        uptime_seconds=payload.uptime_seconds,
        firmware_version=payload.firmware_version,
        network_state=payload.network_state,
        health=payload.health,
        buffer_usage=payload.buffer_usage,
        pending_observations=payload.pending_observations,
        dropped_observations=payload.dropped_observations,
        config_version=payload.config_version,
        config_status=payload.config_status,
    )
    db.add(heartbeat)

    previous_status = scanner.status
    scanner.status = "online"
    scanner.last_heartbeat_at = now
    scanner.last_seen_at = now
    scanner.last_connection_at = scanner.last_connection_at or now
    scanner.uptime_seconds = payload.uptime_seconds
    scanner.firmware_version = payload.firmware_version or scanner.firmware_version
    scanner.hardware_version = payload.hardware_version or scanner.hardware_version
    scanner.network_info = payload.network_state
    if payload.reset_reason:
        scanner.reset_reason = payload.reset_reason
    if payload.config_version is not None:
        scanner.config_version = payload.config_version

    if previous_status != "online":
        create_event(
            db,
            event_type="scanner_connected",
            scanner_id=scanner.id,
            occurred_at=now,
            previous_state=previous_status,
            new_state="online",
            confidence=1.0,
            reason="heartbeat_received",
            dedupe_key=f"scanner-connected:{scanner.id}:{now.strftime('%Y%m%d%H%M')}",
        )

    db.commit()
    return {"accepted": True, "duplicate": False}


def record_gatt_enrichment(
    db: Session,
    scanner: Scanner,
    payload: GATTEnrichmentReportIn,
) -> dict[str, Any] | None:
    existing = db.execute(
        select(DeviceEnrichment).where(
            DeviceEnrichment.scanner_id == scanner.id,
            DeviceEnrichment.source_observation_id == payload.source_observation_id,
            DeviceEnrichment.transport == "ble_gatt",
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {
            "accepted": False,
            "duplicate": True,
            "enrichment_id": existing.id,
            "logical_device_id": existing.logical_device_id,
        }

    source = db.execute(
        select(Observation).where(
            Observation.scanner_id == scanner.id,
            Observation.observation_id == payload.source_observation_id,
        )
    ).scalar_one_or_none()
    if source is None:
        return None
    identity = db.get(ObservedIdentity, source.observed_identity_id)
    logical = db.get(LogicalDevice, source.logical_device_id)
    if identity is None or logical is None:
        raise ValueError("source observation identity is unavailable")
    if normalize_address(payload.address) != normalize_address(identity.address or ""):
        raise ValueError("enrichment address does not match source observation")
    if payload.address_type.strip().lower() != (identity.address_type or "").strip().lower():
        raise ValueError("enrichment address type does not match source observation")

    now = utcnow()
    enriched_at = ensure_utc(payload.enriched_at, now) if payload.enriched_at else now
    if enriched_at > now + timedelta(minutes=5):
        enriched_at = now
    enrichment = payload.gatt_enrichment
    gatt_name = enrichment.device_name.strip() if enrichment.device_name else None
    record = DeviceEnrichment(
        logical_device_id=logical.id,
        observed_identity_id=identity.id,
        scanner_id=scanner.id,
        source_observation_id=payload.source_observation_id,
        enriched_at=enriched_at,
        transport="ble_gatt",
        status=enrichment.status,
        device_name=gatt_name,
        manufacturer_name=enrichment.manufacturer_name,
        model_number=enrichment.model_number,
        serial_number=enrichment.serial_number,
        firmware_revision=enrichment.firmware_revision,
        hardware_revision=enrichment.hardware_revision,
        software_revision=enrichment.software_revision,
        system_id=enrichment.system_id,
        pnp_id=enrichment.pnp_id,
        discovered_services=enrichment.discovered_services,
        characteristic_values=enrichment.characteristic_values,
        error_code=enrichment.error_code,
        attempt_duration_ms=enrichment.attempt_duration_ms,
        details={
            "directly_read": True,
            "pairing_forced": False,
            "address": identity.address,
            "address_type": identity.address_type,
            "report_id": payload.report_id,
            "reported_separately": True,
        },
    )
    db.add(record)
    if gatt_name:
        logical.display_name = gatt_name
        logical.category = logical.category or infer_device_category(gatt_name, [], None)
    db.commit()
    db.refresh(record)
    return {
        "accepted": True,
        "duplicate": False,
        "enrichment_id": record.id,
        "logical_device_id": logical.id,
    }


def create_event(
    db: Session,
    event_type: str,
    occurred_at: datetime,
    scanner_id: str | None = None,
    logical_device_id: str | None = None,
    observed_identity_id: str | None = None,
    previous_state: str | None = None,
    new_state: str | None = None,
    previous_location: str | None = None,
    new_location: str | None = None,
    confidence: float = 0.0,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    severity: str = "info",
) -> DeviceEvent | None:
    if dedupe_key:
        for pending in db.new:
            if isinstance(pending, DeviceEvent) and pending.dedupe_key == dedupe_key:
                return None
        existing = db.execute(select(DeviceEvent).where(DeviceEvent.dedupe_key == dedupe_key)).scalar_one_or_none()
        if existing is not None:
            return None
    event = DeviceEvent(
        event_type=event_type,
        severity=severity,
        scanner_id=scanner_id,
        logical_device_id=logical_device_id,
        observed_identity_id=observed_identity_id,
        occurred_at=ensure_utc(occurred_at),
        previous_state=previous_state,
        new_state=new_state,
        previous_location=previous_location,
        new_location=new_location,
        confidence=confidence,
        reason=reason,
        details=details or {},
        dedupe_key=dedupe_key,
    )
    db.add(event)
    return event


def commit_allowing_event_dedupe_race(db: Session) -> bool:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if "device_events.dedupe_key" not in str(exc):
            raise
        return False
    return True


def find_or_create_observed_identity(db: Session, obs: BLEObservationIn, observed_at: datetime) -> ObservedIdentity:
    address = normalize_address(obs.address)
    randomized = is_randomized_address(obs.address_type, address)

    identity = None
    if address:
        identity = db.execute(
            select(ObservedIdentity).where(
                ObservedIdentity.address == address,
                ObservedIdentity.address_type == (obs.address_type or "unknown"),
            )
        ).scalar_one_or_none()
    if identity is None:
        identity = ObservedIdentity(
            address=address,
            address_type=obs.address_type or "unknown",
            advertised_name=obs.advertised_name,
            local_name=obs.local_name,
            service_uuids=obs.service_uuids,
            service_data=obs.service_data,
            manufacturer_data=obs.manufacturer_data,
            appearance=obs.appearance,
            advertising_flags=obs.advertising_flags,
            raw_advertising_payload=obs.raw_advertising_payload,
            raw_scan_response_payload=obs.raw_scan_response_payload,
            randomized_address=randomized,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            observation_count=0,
        )
        db.add(identity)
        db.flush()
    else:
        identity.advertised_name = obs.advertised_name or identity.advertised_name
        identity.local_name = obs.local_name or identity.local_name
        identity.service_uuids = obs.service_uuids or identity.service_uuids
        identity.service_data = obs.service_data or identity.service_data
        identity.manufacturer_data = obs.manufacturer_data or identity.manufacturer_data
        identity.appearance = obs.appearance or identity.appearance
        identity.advertising_flags = obs.advertising_flags or identity.advertising_flags
        identity.raw_advertising_payload = obs.raw_advertising_payload or identity.raw_advertising_payload
        identity.raw_scan_response_payload = obs.raw_scan_response_payload or identity.raw_scan_response_payload
        identity.randomized_address = identity.randomized_address or randomized
        identity.last_seen_at = latest_datetime(identity.last_seen_at, observed_at)

    identity.observation_count += 1
    return identity


def find_or_create_logical_device(
    db: Session,
    scanner: Scanner,
    identity: ObservedIdentity,
    obs: BLEObservationIn,
    observed_at: datetime,
) -> tuple[LogicalDevice, float, str]:
    address = normalize_address(obs.address)
    latest_observation = db.execute(
        select(Observation)
        .where(Observation.observed_identity_id == identity.id)
        .order_by(desc(Observation.observed_at))
        .limit(1)
    ).scalar_one_or_none()
    if latest_observation is not None:
        logical = db.get(LogicalDevice, latest_observation.logical_device_id)
        if logical is not None and address:
            return logical, 0.95, "same_observed_identity"

    name = obs.local_name or obs.advertised_name
    randomized = identity.randomized_address
    category = obs.device_category or infer_device_category(name, obs.service_uuids)
    scanner_position_available = scanner_position_is_available(scanner)

    if address and not randomized:
        logical_matches = db.execute(
            select(LogicalDevice).where(
                LogicalDevice.primary_address == address,
                LogicalDevice.primary_address_type == (obs.address_type or "unknown"),
            ).order_by(desc(LogicalDevice.last_seen_at))
        ).scalars()
        for logical in logical_matches:
            return logical, 0.98, "stable_address_match"

    logical = LogicalDevice(
        primary_address=address,
        primary_address_type=obs.address_type or "unknown",
        display_name=name or address or "Unknown BLE device",
        # The scanner API can report a manufacturer-data value without giving us
        # the raw AD structure needed to independently verify it.  Set this only
        # after canonical payload parsing in process_observation().
        vendor=None,
        category=category,
        status="newly_detected",
        movement_status="stationary",
        identity_confidence=0.55 if randomized else 0.85,
        location_confidence=0.0,
        current_scanner_id=scanner.id,
        current_zone=scanner.zone or scanner.room,
        latitude=scanner.latitude if scanner_position_available else None,
        longitude=scanner.longitude if scanner_position_available else None,
        location_anchor_observed_at=observed_at,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        observation_count=0,
    )
    db.add(logical)
    db.flush()
    if not randomized:
        create_event(
            db,
            "device_discovered",
            observed_at,
            scanner_id=scanner.id,
            logical_device_id=logical.id,
            observed_identity_id=identity.id,
            new_state="newly_detected",
            confidence=logical.identity_confidence,
            reason="first_logical_observation",
            details={"address": address, "randomized_address": False},
            dedupe_key=f"device-discovered:{logical.id}",
        )
    return logical, logical.identity_confidence, "created_new_logical_device"


def stabilized_movement_result(
    db: Session,
    logical: LogicalDevice,
    metrics: RSSIWindowMetrics,
) -> tuple[MovementResult, str]:
    candidate = movement_result_from_metrics(metrics, logical.movement_status)
    if candidate.status == logical.movement_status or metrics.rssi_metric is None:
        return candidate, candidate.status

    required_prior = RSSI_MOVEMENT_DWELL_OBSERVATIONS - 1
    prior_notes = db.execute(
        select(Observation.processing_notes)
        .where(Observation.logical_device_id == logical.id)
        .order_by(desc(Observation.observed_at), desc(Observation.id))
        .limit(required_prior)
    ).scalars().all()
    prior_candidates = []
    for notes in prior_notes:
        evidence = notes.get("movement_evidence") if isinstance(notes, dict) else None
        prior_candidates.append(
            evidence.get("candidate_status") if isinstance(evidence, dict) else None
        )
    if len(prior_candidates) == required_prior and all(
        status == candidate.status for status in prior_candidates
    ):
        return candidate, candidate.status
    return (
        MovementResult(
            logical.movement_status,
            logical.movement_confidence or 0.0,
            "movement_transition_dwell_pending",
        ),
        candidate.status,
    )


def canonicalize_observation_payload(obs: BLEObservationIn) -> tuple[BLEObservationIn, dict[str, Any]]:
    has_raw_capture = bool(obs.raw_advertising_payload or obs.raw_scan_response_payload)
    if obs.payload_layout_version != 2:
        return obs, {
            "capture_status": "legacy_payload_layout_unverified" if has_raw_capture else "raw_payload_not_captured",
            "field_source": "scanner_api_unverified",
            "payload_layout_version": obs.payload_layout_version,
        }
    if not has_raw_capture:
        return obs, {
            "capture_status": "raw_payload_not_captured",
            "field_source": "scanner_api_unverified",
            "payload_layout_version": obs.payload_layout_version,
        }

    parsed = parse_advertising_and_scan_response(
        obs.raw_advertising_payload,
        obs.raw_scan_response_payload,
    )
    fields = parsed["fields"]
    canonical = obs.model_copy(
        update={
            "advertised_name": fields["name"],
            "local_name": fields["name"],
            "tx_power": fields["tx_power"],
            "service_uuids": fields["service_uuids"],
            "service_data": fields["service_data"],
            "manufacturer_data": fields["manufacturer_data"],
            "appearance": fields["appearance"],
            "advertising_flags": fields["advertising_flags"],
        }
    )
    return canonical, {
        "capture_status": "verified" if parsed["capture_complete"] else "partial_with_parse_errors",
        "field_source": "raw_ad_structures",
        "payload_layout_version": obs.payload_layout_version,
        "ad_parser": parsed,
    }


def effective_observation_time(
    obs: BLEObservationIn,
    batch: ObservationBatchIn,
    received_at: datetime,
) -> tuple[datetime, datetime | None, dict[str, Any]]:
    reported_value = obs.observed_at or obs.scanner_time
    reported_at = ensure_utc(reported_value) if reported_value else None
    source = obs.time_source or batch.time_source or "unspecified"
    clock_sync_age_ms = obs.clock_sync_age_ms if obs.clock_sync_age_ms is not None else batch.clock_sync_age_ms
    is_synchronized = source in {"usb_host_synchronized", "ntp_synchronized"}
    provenance: dict[str, Any] = {
        "reported_time": serialize_datetime(reported_at),
        "source": source,
        "boot_id": obs.boot_id or batch.boot_id,
        "monotonic_ms": obs.monotonic_ms,
        "scan_cycle": obs.scan_cycle,
        "batch_sequence": batch.batch_sequence,
        "clock_sync_age_ms": clock_sync_age_ms,
        "server_received_at": serialize_datetime(received_at),
    }

    if is_synchronized and reported_at is not None and clock_sync_age_ms is not None:
        if clock_sync_age_ms <= TRUSTED_CLOCK_SYNC_MAX_AGE_MS and reported_at <= received_at + MAX_FUTURE_OBSERVATION_SKEW:
            provenance["effective_time_source"] = "scanner_synchronized_clock"
            provenance["time_quality"] = "trusted"
            return reported_at, reported_at, provenance
        provenance["effective_time_source"] = "server_received_fallback"
        provenance["time_quality"] = "untrusted"
        provenance["fallback_reason"] = (
            "clock_sync_age_exceeded" if clock_sync_age_ms > TRUSTED_CLOCK_SYNC_MAX_AGE_MS else "future_timestamp"
        )
        return received_at, reported_at, provenance

    provenance["effective_time_source"] = "server_received_fallback"
    provenance["time_quality"] = "untrusted"
    provenance["fallback_reason"] = "scanner_clock_not_synchronized_or_not_reported"
    return received_at, reported_at, provenance


def process_observation(
    db: Session,
    scanner: Scanner,
    batch: ObservationBatchIn,
    obs: BLEObservationIn,
    received_at: datetime,
) -> tuple[bool, dict[str, Any]]:
    address = normalize_address(obs.address)
    if is_synthetic_address_pattern(address):
        return False, {
            "ignored": True,
            "reason": "synthetic_address_pattern",
            "address": address,
            "observation_id": obs.observation_id,
        }

    duplicate = db.execute(
        select(Observation).where(
            Observation.scanner_id == scanner.id,
            Observation.observation_id == obs.observation_id,
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        return False, {"duplicate": True, "observation_id": obs.observation_id}

    obs, capture_provenance = canonicalize_observation_payload(obs)
    observed_at, scanner_time, time_provenance = effective_observation_time(obs, batch, received_at)
    identity = find_or_create_observed_identity(db, obs, observed_at)
    logical, identity_confidence, correlation_reason = find_or_create_logical_device(db, scanner, identity, obs, observed_at)

    previous_status = logical.status
    previous_movement = logical.movement_status
    previous_band = logical.proximity_band
    previous_scanner_id = logical.current_scanner_id
    previous_zone = logical.current_zone
    previous_latitude = logical.latitude
    previous_longitude = logical.longitude
    previous_anchor_observed_at = logical.location_anchor_observed_at
    previous_smoothed = logical.smoothed_rssi
    first_logical_observation = logical.observation_count == 0
    is_current_observation = (
        logical.last_seen_at is None
        or observed_at >= ensure_utc(logical.last_seen_at)
    )

    smoothed = (
        float(obs.rssi)
        if not is_current_observation
        else float(obs.rssi)
    )
    rssi_metrics = (
        rssi_window_metrics(rssi_samples_for_window_metric(db, logical.id, scanner.id, obs.rssi))
        if is_current_observation
        else rssi_window_metrics({})
    )
    # The paper uses a five-reading window average. Use that same average for
    # the current scanner's displayed RSSI whenever the window exists; raw RSSI
    # is retained until enough readings have arrived.
    smoothed = rssi_metrics.current_window_means.get(scanner.id, smoothed)
    proximity = infer_proximity_from_rssi(smoothed, rssi_metrics)
    band = signal_band_with_hysteresis(smoothed, previous_band)
    proximity_model = proximity_model_payload(proximity, obs.rssi, smoothed, rssi_metrics)
    proximity_model["raw_band"] = signal_band_from_rssi(smoothed)
    proximity_model["band"] = band
    proximity_model["band_hysteresis_db"] = SIGNAL_BAND_HYSTERESIS_DB
    distance = proximity.distance_m
    movement, movement_candidate = stabilized_movement_result(db, logical, rssi_metrics)

    manufacturer_profile = analyze_manufacturer_data(obs.manufacturer_data)
    raw_capture_verified = capture_provenance.get("capture_status") == "verified"
    device_classification = classify_flipper_zero(
        service_uuids=obs.service_uuids,
        capture_verified=raw_capture_verified,
        name=obs.local_name or obs.advertised_name,
        address=obs.address,
        address_type=obs.address_type,
        tx_power=obs.tx_power,
        advertising_type=obs.advertising_type,
        connectable=obs.connectable,
        advertising_flags=obs.advertising_flags,
        observation_count=identity.observation_count,
    )
    # A Bluetooth SIG Company Identifier identifies the manufacturer-data
    # namespace, not necessarily the physical product manufacturer.  We expose
    # it only when the ID came from a successfully parsed raw ADV capture.
    vendor = manufacturer_profile.get("company_name") if raw_capture_verified else None
    if device_classification:
        category = "flipper_zero"
    else:
        category = obs.device_category or infer_device_category(
            obs.local_name or obs.advertised_name,
            obs.service_uuids,
            obs.manufacturer_data if raw_capture_verified else None,
        )
    estimate_zone = scanner.zone or scanner.room
    estimate_location_confidence = location_confidence_for_proximity(proximity, scanner)
    scanner_position_available = scanner_position_is_available(scanner)
    scanner_position_current = scanner_position_is_current(scanner, observed_at)
    scanner_position_changed = (
        previous_latitude != scanner.latitude
        or previous_longitude != scanner.longitude
    )
    updates_location_anchor = is_current_observation and (
        first_logical_observation
        or logical.current_scanner_id is None
        or logical.current_scanner_id != scanner.id
        or logical.current_zone != estimate_zone
        or (scanner_position_current and scanner_position_changed)
    )
    identity_basis = presence_identity_bases(db, [logical]).get(logical.id)
    unresolved_random = identity_basis == "unresolved_randomized_address"

    if is_current_observation:
        returned = observed_again_status(logical.status)
        if logical.status == "identity_expired":
            logical.status = "active"
            if not unresolved_random:
                create_event(
                    db,
                    "device_identity_reappeared",
                    observed_at,
                    scanner_id=scanner.id,
                    logical_device_id=logical.id,
                    observed_identity_id=identity.id,
                    previous_state=previous_status,
                    new_state="active",
                    confidence=identity_confidence,
                    reason="randomized_address_observed_again",
                    dedupe_key=f"identity-reappeared:{logical.id}:{observed_at.strftime('%Y%m%d%H%M')}",
                )
        elif returned:
            logical.status = "active" if returned[0] != "returned" else "returned"
            if not unresolved_random:
                create_event(
                    db,
                    "device_returned" if returned[0] == "returned" else "device_seen",
                    observed_at,
                    scanner_id=scanner.id,
                    logical_device_id=logical.id,
                    observed_identity_id=identity.id,
                    previous_state=previous_status,
                    new_state=logical.status,
                    confidence=0.85,
                    reason=returned[1],
                    dedupe_key=f"{returned[0]}:{logical.id}:{observed_at.strftime('%Y%m%d%H%M')}",
                )
        elif logical.status in {"newly_detected", "returned"}:
            logical.status = "active"

        logical.movement_status = movement.status
        logical.movement_confidence = movement.confidence
        logical.identity_confidence = max(logical.identity_confidence or 0.0, identity_confidence)
        logical.location_confidence = estimate_location_confidence
        if logical.location_anchor_observed_at is None:
            logical.location_anchor_observed_at = logical.last_seen_at or observed_at
        if updates_location_anchor:
            logical.current_scanner_id = scanner.id
            logical.current_zone = estimate_zone
            if scanner_position_current:
                logical.latitude = scanner.latitude
                logical.longitude = scanner.longitude
            elif previous_scanner_id != scanner.id:
                logical.latitude = None
                logical.longitude = None
            logical.location_anchor_observed_at = observed_at
        logical.proximity_band = band
        logical.estimated_distance_m = distance
        logical.smoothed_rssi = smoothed
        logical.last_seen_at = observed_at
        logical.display_name = logical.display_name or obs.local_name or obs.advertised_name
        if vendor:
            logical.vendor = vendor
        logical.category = category if device_classification else logical.category or category
    logical.observation_count += 1

    anchor_changed = updates_location_anchor and previous_scanner_id is not None and (
        previous_scanner_id != logical.current_scanner_id
        or previous_zone != logical.current_zone
        or previous_latitude != logical.latitude
        or previous_longitude != logical.longitude
    )
    anchor_displacement_m = coordinate_distance_m(
        previous_latitude,
        previous_longitude,
        logical.latitude,
        logical.longitude,
    )
    meaningful_location_changed = anchor_changed and (
        previous_scanner_id != logical.current_scanner_id
        or previous_zone != logical.current_zone
        or (
            anchor_displacement_m is not None
            and anchor_displacement_m >= LOCATION_EVENT_MIN_DISTANCE_M
        )
    )
    if meaningful_location_changed:
        location_reason = (
            "observed_by_different_scanner"
            if previous_scanner_id != logical.current_scanner_id
            else "observed_after_scanner_position_changed"
        )
        create_event(
            db,
            "device_location_changed",
            observed_at,
            scanner_id=scanner.id,
            logical_device_id=logical.id,
            observed_identity_id=identity.id,
            previous_location=previous_zone or previous_scanner_id,
            new_location=estimate_zone or scanner.id,
            confidence=logical.location_confidence,
            reason=location_reason,
            details={
                "previous_scanner_id": previous_scanner_id,
                "current_scanner_id": logical.current_scanner_id,
                "previous_zone": previous_zone,
                "current_zone": logical.current_zone,
                "previous_anchor_latitude": previous_latitude,
                "previous_anchor_longitude": previous_longitude,
                "current_anchor_latitude": logical.latitude,
                "current_anchor_longitude": logical.longitude,
                "previous_anchor_observed_at": serialize_datetime(previous_anchor_observed_at),
                "current_anchor_observed_at": serialize_datetime(logical.location_anchor_observed_at),
                "anchor_displacement_m": anchor_displacement_m,
                "scanner_location_source": scanner.location_source,
                "scanner_location_accuracy_m": scanner.location_accuracy_m,
            },
            dedupe_key=f"device-location:{logical.id}:{obs.observation_id}",
        )

    if (
        is_current_observation
        and not unresolved_random
        and previous_movement != movement.status
        and movement.status in {"probably_moving", "signal_stable"}
    ):
        create_event(
            db,
            "device_movement_changed",
            observed_at,
            scanner_id=scanner.id,
            logical_device_id=logical.id,
            observed_identity_id=identity.id,
            previous_state=previous_movement,
            new_state=movement.status,
            previous_location=previous_band,
            new_location=band,
            confidence=movement.confidence,
            reason=movement.reason,
            details={"previous_smoothed_rssi": previous_smoothed, "smoothed_rssi": smoothed, "rssi": obs.rssi},
            dedupe_key=f"movement:{logical.id}:{obs.observation_id}",
        )

    signal_band_changed = (
        is_current_observation
        and not first_logical_observation
        and previous_band != band
    )
    if signal_band_changed and not meaningful_location_changed and not unresolved_random:
        create_event(
            db,
            "device_signal_band_changed",
            observed_at,
            scanner_id=scanner.id,
            logical_device_id=logical.id,
            observed_identity_id=identity.id,
            previous_location=previous_band,
            new_location=band,
            confidence=logical.location_confidence,
            reason="hysteresis_confirmed_signal_band_change",
            details={"zone": estimate_zone, "distance_m": distance, "proximity_model": proximity_model},
            dedupe_key=f"signal-band:{logical.id}:{obs.observation_id}",
        )

    observation = Observation(
        scanner_id=scanner.id,
        batch_id=batch.batch_id,
        observation_id=obs.observation_id,
        observed_identity_id=identity.id,
        logical_device_id=logical.id,
        observed_at=observed_at,
        scanner_time=scanner_time,
        server_received_at=received_at,
        processed_at=utcnow(),
        rssi=obs.rssi,
        tx_power=obs.tx_power,
        estimated_distance_m=distance,
        advertising_type=obs.advertising_type,
        service_uuids=obs.service_uuids,
        service_data=obs.service_data,
        manufacturer_data=obs.manufacturer_data,
        appearance=obs.appearance,
        advertising_flags=obs.advertising_flags,
        connectable=obs.connectable,
        raw_advertising_payload=obs.raw_advertising_payload,
        raw_scan_response_payload=obs.raw_scan_response_payload,
        packet_length=obs.packet_length,
        firmware_version=batch.firmware_version,
        scanner_uptime_seconds=batch.scanner_uptime_seconds,
        processing_notes={
            "correlation_reason": correlation_reason,
            **({"device_classification": device_classification} if device_classification else {}),
            "manufacturer_profile": manufacturer_profile,
            "proximity_model": proximity_model,
            "movement_evidence": {
                "candidate_status": movement_candidate,
                "applied_status": movement.status,
                "reason": movement.reason,
                "dwell_observations": RSSI_MOVEMENT_DWELL_OBSERVATIONS,
            },
            "updates_current_location": updates_location_anchor,
            "location_anchor_policy": "latest_observation_with_current_scanner_position",
            "anchor_snapshot": {
                "scanner_id": logical.current_scanner_id,
                "zone": logical.current_zone,
                "latitude": logical.latitude,
                "longitude": logical.longitude,
                "anchored_at": serialize_datetime(logical.location_anchor_observed_at),
                "scanner_location_source": scanner.location_source,
                "scanner_location_accuracy_m": scanner.location_accuracy_m,
                "scanner_position_current": scanner_position_current,
            },
            "capture_provenance": capture_provenance,
            "time_provenance": time_provenance,
        },
    )
    db.add(observation)
    estimate_reasons = []
    if first_logical_observation:
        estimate_reasons.append("first_observation")
    if anchor_changed:
        estimate_reasons.append("anchor_changed")
    if signal_band_changed:
        estimate_reasons.append("signal_band_changed")
    if is_current_observation and estimate_reasons:
        db.add(DeviceLocationEstimate(
            logical_device_id=logical.id,
            scanner_id=scanner.id,
            estimated_at=observed_at,
            zone=estimate_zone,
            proximity_band=band,
            estimated_distance_m=distance,
            confidence=estimate_location_confidence,
            method=proximity.method,
            details={
                "rssi": obs.rssi,
                "smoothed_rssi": smoothed,
                "proximity_model": proximity_model,
                "scanner_latitude": scanner.latitude,
                "scanner_longitude": scanner.longitude,
                "scanner_location_source": scanner.location_source,
                "scanner_location_observed_at": serialize_datetime(scanner.location_observed_at),
                "scanner_location_accuracy_m": scanner.location_accuracy_m,
                "scanner_position_available": scanner_position_available,
                "scanner_position_current": scanner_position_current,
                "updates_current_anchor": updates_location_anchor,
                "anchor_scanner_id": logical.current_scanner_id,
                "anchor_latitude": logical.latitude,
                "anchor_longitude": logical.longitude,
                "record_reasons": estimate_reasons,
            },
        ))

    scanner.last_seen_at = received_at
    return True, {
        "duplicate": False,
        "logical_device_id": logical.id,
        "observed_identity_id": identity.id,
        "randomized_address": bool(identity.randomized_address),
        "correlation_reason": correlation_reason,
        "status": logical.status,
    }


def location_confidence_for_proximity(proximity: Any, scanner: Scanner) -> float:
    # A scanner's configured zone is its own installation location. RSSI from
    # one scanner is not evidence that the device occupies that exact zone.
    return 0.0


def _correlation_setting(db: Session, key: str) -> Any:
    setting = db.get(SystemSetting, key)
    return setting.value if setting is not None else DEFAULT_SETTINGS[key][0]


def _bounded_correlation_number(
    value: Any,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not number == number or number < minimum or number > maximum:
        return default
    return number


def correlation_config(db: Session) -> dict[str, Any]:
    configured_alpha = _correlation_setting(db, "correlation_alpha")
    alpha: float | None = None
    if configured_alpha is not None:
        candidate = _bounded_correlation_number(configured_alpha, 0.0, 0.000001, 10_000.0)
        if candidate > 0:
            alpha = candidate
    return {
        "rotation_window_seconds": _bounded_correlation_number(
            _correlation_setting(db, "correlation_rotation_window_seconds"), 20.0, 1.0, 3600.0
        ),
        "evaluation_window_seconds": _bounded_correlation_number(
            _correlation_setting(db, "correlation_evaluation_window_seconds"), 20.0, 1.0, 3600.0
        ),
        "min_regression_samples": int(
            _bounded_correlation_number(
                _correlation_setting(db, "correlation_min_regression_samples"), 3.0, 2.0, 100.0
            )
        ),
        "alpha": alpha,
        "unmatched_cost": _bounded_correlation_number(
            _correlation_setting(db, "correlation_unmatched_cost_seconds"), 30.0, 0.001, 86_400.0
        ),
        "token_carryover_max_seconds": _bounded_correlation_number(
            _correlation_setting(db, "correlation_token_carryover_max_seconds"), 3600.0, 1.0, 604_800.0
        ),
        "token_min_observations": int(
            _bounded_correlation_number(
                _correlation_setting(db, "correlation_token_min_observations"), 2.0, 1.0, 100.0
            )
        ),
        "token_rules": parse_token_rules(_correlation_setting(db, "correlation_token_rules")),
    }


def _identity_observations(db: Session, identity_id: str) -> list[Observation]:
    return db.execute(
        select(Observation)
        .where(Observation.observed_identity_id == identity_id)
        .order_by(Observation.observed_at)
    ).scalars().all()


def _trusted_rssi_points(
    observations: list[Observation],
    start: datetime,
    end: datetime,
    scanner_id: str,
) -> list[tuple[datetime, int]]:
    points: list[tuple[datetime, int]] = []
    for observation in observations:
        observed_at = ensure_utc(observation.observed_at)
        if observation.scanner_id != scanner_id or observed_at < start or observed_at > end:
            continue
        notes = observation.processing_notes if isinstance(observation.processing_notes, dict) else {}
        provenance = notes.get("time_provenance") if isinstance(notes, dict) else None
        if not isinstance(provenance, dict) or provenance.get("time_quality") != "trusted":
            continue
        points.append((observed_at, observation.rssi))
    return points


def _identity_token_evidence(observations: list[Observation], rules: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for observation in observations:
        notes = observation.processing_notes if isinstance(observation.processing_notes, dict) else {}
        capture = notes.get("capture_provenance") if isinstance(notes, dict) else None
        parser = capture.get("ad_parser") if isinstance(capture, dict) else None
        for token_hash, token in extract_approved_tokens(parser, rules).items():
            item = evidence.setdefault(token_hash, {**token, "observation_count": 0})
            item["observation_count"] += 1
    return evidence


def _logical_for_identity(observations: list[Observation]) -> LogicalDevice | None:
    if not observations:
        return None
    # All observations of an observed identity are reassigned when an accepted
    # correlation is merged, so the latest row identifies the canonical record.
    return observations[-1].logical_device


def _correlation_exists(
    db: Session,
    predecessor_identity_id: str,
    successor_identity_id: str,
    method: str,
) -> bool:
    return (
        db.execute(
            select(DeviceIdentityCorrelation.id).where(
                DeviceIdentityCorrelation.predecessor_identity_id == predecessor_identity_id,
                DeviceIdentityCorrelation.successor_identity_id == successor_identity_id,
                DeviceIdentityCorrelation.method == method,
            )
        ).scalar_one_or_none()
        is not None
    )


def _accepted_correlation_for_successor(db: Session, successor_identity_id: str) -> bool:
    return (
        db.execute(
            select(DeviceIdentityCorrelation.id).where(
                DeviceIdentityCorrelation.successor_identity_id == successor_identity_id,
                DeviceIdentityCorrelation.status == "accepted",
            )
        ).scalar_one_or_none()
        is not None
    )


def _record_identity_correlation(
    db: Session,
    *,
    predecessor_identity: ObservedIdentity,
    successor_identity: ObservedIdentity,
    predecessor_device: LogicalDevice,
    successor_device: LogicalDevice,
    method: str,
    status: str,
    details: dict[str, Any],
    time_difference_seconds: float | None = None,
    rssi_difference_db: float | None = None,
    assignment_cost: float | None = None,
    alpha: float | None = None,
    search_window_seconds: float | None = None,
    evaluation_window_seconds: float | None = None,
    accepted_at: datetime | None = None,
) -> DeviceIdentityCorrelation | None:
    if _correlation_exists(db, predecessor_identity.id, successor_identity.id, method):
        return None
    correlation = DeviceIdentityCorrelation(
        predecessor_identity_id=predecessor_identity.id,
        successor_identity_id=successor_identity.id,
        predecessor_logical_device_id=predecessor_device.id,
        successor_logical_device_id=successor_device.id,
        method=method,
        status=status,
        time_difference_seconds=time_difference_seconds,
        rssi_difference_db=rssi_difference_db,
        assignment_cost=assignment_cost,
        alpha=alpha,
        search_window_seconds=search_window_seconds,
        evaluation_window_seconds=evaluation_window_seconds,
        details=details,
        accepted_at=accepted_at,
    )
    db.add(correlation)
    return correlation


def _merge_accepted_identity_correlation(
    db: Session,
    correlation: DeviceIdentityCorrelation,
    predecessor_device: LogicalDevice,
    successor_device: LogicalDevice,
    successor_observations: list[Observation],
) -> None:
    """Make the successor's latest verified scanner location canonical.

    The older location history is retained by reassigning estimates and events
    to the predecessor record.  This is the Tebet -> Bekasi flow, but only
    after the correlation has been accepted by an approved evidence path.
    """

    if predecessor_device.id == successor_device.id:
        return
    successor_seen_at = ensure_utc(successor_device.last_seen_at)
    merge_result = merge_logical_devices(
        db,
        canonical=predecessor_device,
        merged=successor_device,
    )
    previous_scanner_id = merge_result.previous_scanner_id
    previous_zone = merge_result.previous_zone
    moved_between_scanners = (
        previous_scanner_id
        and previous_scanner_id != predecessor_device.current_scanner_id
    )
    if moved_between_scanners:
        predecessor_device.movement_status = "relocated_between_scanners"
        predecessor_device.movement_confidence = 0.0
    create_event(
        db,
        "device_identity_correlated",
        successor_seen_at,
        scanner_id=predecessor_device.current_scanner_id,
        logical_device_id=predecessor_device.id,
        observed_identity_id=correlation.successor_identity_id,
        previous_location=previous_zone or previous_scanner_id,
        new_location=predecessor_device.current_zone or predecessor_device.current_scanner_id,
        confidence=0.0,
        reason=correlation.method,
        details={
            "correlation_method": correlation.method,
            "correlation_status": correlation.status,
            "identity_evidence": correlation.details,
            "previous_scanner_id": previous_scanner_id,
            "current_scanner_id": predecessor_device.current_scanner_id,
            "successor_observation_count": len(successor_observations),
        },
        dedupe_key=(
            f"identity-correlation:{correlation.predecessor_identity_id}:"
            f"{correlation.successor_identity_id}:{correlation.method}"
        ),
    )


APPLE_TRANSITION_WINDOW_SECONDS = 30.0
APPLE_PROPOSAL_MIN_SCORE = 0.55


def _apple_messages(observation: Observation) -> list[dict[str, Any]]:
    profile = analyze_manufacturer_data(observation.manufacturer_data)
    messages = profile.get("continuity_messages")
    if profile.get("company_id") != "0x004C" or not isinstance(messages, list):
        return []
    return [message for message in messages if isinstance(message, dict) and message.get("complete") is True]


def _apple_boundary_evidence(observations: list[Observation]) -> dict[str, Any]:
    subtypes: set[str] = set()
    transition_tags: set[str] = set()
    stable_tokens: set[str] = set()
    handoff_ivs: list[int] = []
    proximity_models: set[str] = set()
    for observation in observations:
        for message in _apple_messages(observation):
            name = str(message.get("name") or "")
            if name:
                subtypes.add(name)
            if name in {"nearby_info", "nearby_action"} and message.get("authentication_tag_hash"):
                transition_tags.add(str(message["authentication_tag_hash"]))
            if name == "tethering_target_presence" and message.get("identifier_hash"):
                stable_tokens.add(str(message["identifier_hash"]))
            if name == "magic_switch" and message.get("data_hash"):
                stable_tokens.add(str(message["data_hash"]))
            if name == "handoff" and isinstance(message.get("iv"), int):
                handoff_ivs.append(int(message["iv"]))
            if name == "proximity_pairing" and message.get("device_model_code"):
                proximity_models.add(str(message["device_model_code"]))
    return {
        "subtypes": subtypes,
        "transition_tags": transition_tags,
        "stable_tokens": stable_tokens,
        "handoff_ivs": handoff_ivs,
        "proximity_models": proximity_models,
    }


def _latest_gatt_model(db: Session, logical_device_id: str) -> str | None:
    return db.execute(
        select(DeviceEnrichment.model_number)
        .where(
            DeviceEnrichment.logical_device_id == logical_device_id,
            DeviceEnrichment.status.in_(("success", "partial")),
            DeviceEnrichment.model_number.is_not(None),
        )
        .order_by(desc(DeviceEnrichment.enriched_at))
        .limit(1)
    ).scalar_one_or_none()


def _apple_transition_candidate(
    predecessor_observations: list[Observation],
    successor_observations: list[Observation],
    predecessor_gatt_model: str | None,
    successor_gatt_model: str | None,
) -> dict[str, Any] | None:
    predecessor_last = predecessor_observations[-1]
    successor_first = successor_observations[0]
    gap_seconds = (
        ensure_utc(successor_first.observed_at) - ensure_utc(predecessor_last.observed_at)
    ).total_seconds()
    if gap_seconds < 0 or gap_seconds > APPLE_TRANSITION_WINDOW_SECONDS:
        return None

    predecessor_boundary = predecessor_observations[-3:]
    successor_boundary = successor_observations[:3]
    predecessor = _apple_boundary_evidence(predecessor_boundary)
    successor = _apple_boundary_evidence(successor_boundary)
    subtype_overlap = sorted(predecessor["subtypes"] & successor["subtypes"])
    if not subtype_overlap:
        return None

    matching_transition_tags = sorted(
        predecessor["transition_tags"] & successor["transition_tags"]
    )
    matching_stable_tokens = sorted(
        predecessor["stable_tokens"] & successor["stable_tokens"]
    )
    matching_proximity_models = sorted(
        predecessor["proximity_models"] & successor["proximity_models"]
    )
    handoff_delta = None
    if predecessor["handoff_ivs"] and successor["handoff_ivs"]:
        handoff_delta = (
            successor["handoff_ivs"][0] - predecessor["handoff_ivs"][-1]
        ) % 65536
        if handoff_delta > 32:
            handoff_delta = None

    predecessor_rssi = float(median([item.rssi for item in predecessor_boundary]))
    successor_rssi = float(median([item.rssi for item in successor_boundary]))
    rssi_difference = abs(successor_rssi - predecessor_rssi)
    gatt_model_match = bool(
        predecessor_gatt_model
        and successor_gatt_model
        and predecessor_gatt_model == successor_gatt_model
    )
    protocol_transition_evidence = bool(
        matching_transition_tags or matching_stable_tokens or handoff_delta is not None
    )
    composite_model_evidence = (
        gatt_model_match
        and gap_seconds <= 15
        and rssi_difference <= 12
    )
    if not protocol_transition_evidence and not composite_model_evidence:
        return None

    score = 0.08
    score += 0.45 if matching_transition_tags else 0.0
    score += 0.55 if matching_stable_tokens else 0.0
    score += 0.40 if handoff_delta is not None else 0.0
    score += 0.30 if gatt_model_match else 0.0
    score += 0.08 if matching_proximity_models else 0.0
    score += 0.10 if gap_seconds <= 5 else 0.06 if gap_seconds <= 15 else 0.03
    score += 0.08 if rssi_difference <= 6 else 0.04 if rssi_difference <= 12 else 0.0
    score = min(score, 0.99)
    if score < APPLE_PROPOSAL_MIN_SCORE:
        return None
    return {
        "score": score,
        "gap_seconds": gap_seconds,
        "rssi_difference_db": rssi_difference,
        "subtype_overlap": subtype_overlap,
        "matching_transition_tag_hashes": matching_transition_tags,
        "matching_stable_token_hashes": matching_stable_tokens,
        "handoff_iv_delta": handoff_delta,
        "matching_proximity_model_codes": matching_proximity_models,
        "gatt_model_match": gatt_model_match,
        "gatt_model": successor_gatt_model if gatt_model_match else None,
        "protocol_transition_evidence": protocol_transition_evidence,
    }


def run_apple_continuity_correlation(
    db: Session,
    successor_identity_ids: set[str] | None,
) -> int:
    if not successor_identity_ids:
        return 0
    successors = db.execute(
        select(ObservedIdentity).where(
            ObservedIdentity.id.in_(successor_identity_ids),
            ObservedIdentity.randomized_address.is_(True),
        )
    ).scalars().all()
    proposals = 0
    gatt_models: dict[str, str | None] = {}
    observations_by_identity: dict[str, list[Observation]] = {}

    def observations_for(identity_id: str) -> list[Observation]:
        if identity_id not in observations_by_identity:
            observations_by_identity[identity_id] = _identity_observations(db, identity_id)
        return observations_by_identity[identity_id]

    def gatt_model_for(logical_device_id: str) -> str | None:
        if logical_device_id not in gatt_models:
            gatt_models[logical_device_id] = _latest_gatt_model(db, logical_device_id)
        return gatt_models[logical_device_id]

    for successor in successors:
        successor_observations = observations_for(successor.id)
        successor_device = _logical_for_identity(successor_observations)
        if not successor_observations or successor_device is None:
            continue
        successor_first = successor_observations[0]
        if not _apple_messages(successor_first):
            continue
        successor_at = ensure_utc(successor_first.observed_at)
        predecessor_ids = set(
            db.execute(
                select(Observation.observed_identity_id).where(
                    Observation.scanner_id == successor_first.scanner_id,
                    Observation.observed_identity_id != successor.id,
                    Observation.observed_at >= successor_at - timedelta(
                        seconds=APPLE_TRANSITION_WINDOW_SECONDS
                    ),
                    Observation.observed_at <= successor_at,
                )
            ).scalars()
        )
        candidates: list[
            tuple[dict[str, Any], ObservedIdentity, LogicalDevice, list[Observation]]
        ] = []
        successor_model = gatt_model_for(successor_device.id)
        for predecessor_id in predecessor_ids:
            predecessor = db.get(ObservedIdentity, predecessor_id)
            if predecessor is None:
                continue
            predecessor_observations = observations_for(predecessor.id)
            predecessor_device = _logical_for_identity(predecessor_observations)
            if (
                not predecessor_observations
                or predecessor_device is None
                or predecessor_device.id == successor_device.id
                or ensure_utc(predecessor_observations[-1].observed_at) > successor_at
            ):
                continue
            predecessor_model = gatt_model_for(predecessor_device.id)
            evidence = _apple_transition_candidate(
                predecessor_observations,
                successor_observations,
                predecessor_model,
                successor_model,
            )
            if evidence is not None:
                candidates.append(
                    (evidence, predecessor, predecessor_device, predecessor_observations)
                )
        if not candidates:
            continue
        candidates.sort(key=lambda item: item[0]["score"], reverse=True)
        evidence, predecessor, predecessor_device, _ = candidates[0]
        if _correlation_exists(
            db,
            predecessor.id,
            successor.id,
            "apple_continuity_transition_v1",
        ):
            continue
        runner_up_score = candidates[1][0]["score"] if len(candidates) > 1 else None
        score_margin = (
            evidence["score"] - runner_up_score
            if runner_up_score is not None
            else None
        )
        correlation = _record_identity_correlation(
            db,
            predecessor_identity=predecessor,
            successor_identity=successor,
            predecessor_device=predecessor_device,
            successor_device=successor_device,
            method="apple_continuity_transition_v1",
            status="proposal",
            time_difference_seconds=evidence["gap_seconds"],
            rssi_difference_db=evidence["rssi_difference_db"],
            search_window_seconds=APPLE_TRANSITION_WINDOW_SECONDS,
            details={
                **evidence,
                "scanner_id": successor_first.scanner_id,
                "evidence_score": evidence["score"],
                "candidate_count": len(candidates),
                "runner_up_score": runner_up_score,
                "score_margin": score_margin,
                "automatic_acceptance": False,
                "identity_claim": "possible_match_not_confirmed_physical_identity",
                "research_basis": [
                    "apple_continuity_tlv",
                    "nearby_info_auth_tag_transition",
                    "handoff_monotonic_iv",
                ],
            },
        )
        if correlation is not None:
            proposals += 1
    return proposals


def run_identity_correlation(
    db: Session,
    include_statistical_review: bool = True,
    successor_identity_ids: set[str] | None = None,
) -> dict[str, int]:
    """Run auditable address-rotation correlation after a batch is flushed.

    This implements the time/RSSI cost from Akiyama and Taniguchi for proposals
    only.  It deliberately does not treat a cost as a probability or merge
    unknown devices by name, vendor, UUID, RSSI, location, or time alone.
    """

    config = correlation_config(db)
    apple_proposals = run_apple_continuity_correlation(
        db,
        successor_identity_ids,
    )
    if not config["token_rules"] and not include_statistical_review:
        return {"accepted": 0, "proposals": apple_proposals}
    eligible_successor_ids = successor_identity_ids
    window_start: datetime | None = None
    window_end: datetime | None = None
    if successor_identity_ids is not None:
        successor_records = db.execute(
            select(ObservedIdentity).where(
                ObservedIdentity.id.in_(successor_identity_ids),
                ObservedIdentity.randomized_address.is_(True),
            )
        ).scalars().all()
        eligible_successors = [
            identity
            for identity in successor_records
            if (
                config["token_rules"]
                and identity.observation_count >= config["token_min_observations"]
            )
            or (
                include_statistical_review
                and identity.observation_count >= config["min_regression_samples"]
            )
        ]
        if not eligible_successors:
            return {"accepted": 0, "proposals": apple_proposals}
        eligible_successor_ids = {identity.id for identity in eligible_successors}
        earliest_successor = min(
            ensure_utc(identity.first_seen_at) for identity in eligible_successors
        )
        latest_successor = max(
            ensure_utc(identity.last_seen_at) for identity in eligible_successors
        )
        lookback_seconds = max(
            config["rotation_window_seconds"] + config["evaluation_window_seconds"],
            config["token_carryover_max_seconds"] if config["token_rules"] else 0.0,
        )
        window_start = earliest_successor - timedelta(seconds=lookback_seconds)
        window_end = latest_successor + timedelta(
            seconds=config["evaluation_window_seconds"]
        )
        identities = db.execute(
            select(ObservedIdentity)
            .where(
                ObservedIdentity.randomized_address.is_(True),
                ObservedIdentity.last_seen_at >= window_start,
                ObservedIdentity.first_seen_at <= window_end,
            )
            .order_by(desc(ObservedIdentity.last_seen_at))
            .limit(500)
        ).scalars().all()
        existing_ids = {identity.id for identity in identities}
        identities.extend(
            identity
            for identity in eligible_successors
            if identity.id not in existing_ids
        )
    else:
        identities = db.execute(
            select(ObservedIdentity)
            .where(ObservedIdentity.randomized_address.is_(True))
            .order_by(desc(ObservedIdentity.last_seen_at))
            .limit(500)
        ).scalars().all()

    identity_ids = {identity.id for identity in identities}
    observation_query = (
        select(Observation)
        .where(Observation.observed_identity_id.in_(identity_ids))
        .order_by(Observation.observed_identity_id, Observation.observed_at)
    )
    if window_start is not None and window_end is not None:
        observation_query = observation_query.where(
            Observation.observed_at >= window_start,
            Observation.observed_at <= window_end,
        )
    observations_by_identity: dict[str, list[Observation]] = {
        identity_id: [] for identity_id in identity_ids
    }
    for observation in db.execute(observation_query).scalars():
        observations_by_identity[observation.observed_identity_id].append(observation)
    logical_device_ids = {
        observations[-1].logical_device_id
        for observations in observations_by_identity.values()
        if observations
    }
    logical_devices_by_id = {
        device.id: device
        for device in db.execute(
            select(LogicalDevice).where(LogicalDevice.id.in_(logical_device_ids))
        ).scalars()
    }
    metadata: dict[str, tuple[Observation, Observation, LogicalDevice]] = {}
    for identity in identities:
        observations = observations_by_identity[identity.id]
        device = (
            logical_devices_by_id.get(observations[-1].logical_device_id)
            if observations
            else None
        )
        if observations and device is not None:
            metadata[identity.id] = (observations[0], observations[-1], device)

    accepted = 0
    proposals = apple_proposals
    token_evidence = {
        identity.id: _identity_token_evidence(observations_by_identity[identity.id], config["token_rules"])
        for identity in identities
    }

    # Direct carryover is intentionally unavailable until an operator has
    # approved a protocol-specific, locally unique AD token rule.
    if config["token_rules"]:
        successors = (
            [identity for identity in identities if identity.id in eligible_successor_ids]
            if eligible_successor_ids is not None
            else identities
        )
        for successor in successors:
            if successor.id not in metadata or _accepted_correlation_for_successor(db, successor.id):
                continue
            successor_first, _, successor_device = metadata[successor.id]
            successor_at = ensure_utc(successor_first.observed_at)
            for token_hash, token in token_evidence[successor.id].items():
                if token["observation_count"] < config["token_min_observations"]:
                    continue
                candidates: list[tuple[ObservedIdentity, LogicalDevice]] = []
                for predecessor in identities:
                    if predecessor.id == successor.id or predecessor.id not in metadata:
                        continue
                    predecessor_token = token_evidence[predecessor.id].get(token_hash)
                    if predecessor_token is None or predecessor_token["observation_count"] < config["token_min_observations"]:
                        continue
                    _, predecessor_last, predecessor_device = metadata[predecessor.id]
                    gap = (successor_at - ensure_utc(predecessor_last.observed_at)).total_seconds()
                    if 0 <= gap <= config["token_carryover_max_seconds"]:
                        candidates.append((predecessor, predecessor_device))
                if len(candidates) != 1:
                    continue
                predecessor, predecessor_device = candidates[0]
                if _correlation_exists(db, predecessor.id, successor.id, "approved_ad_token_carryover"):
                    continue
                _, predecessor_last, _ = metadata[predecessor.id]
                time_delta = (successor_at - ensure_utc(predecessor_last.observed_at)).total_seconds()
                correlation = _record_identity_correlation(
                    db,
                    predecessor_identity=predecessor,
                    successor_identity=successor,
                    predecessor_device=predecessor_device,
                    successor_device=successor_device,
                    method="approved_ad_token_carryover",
                    status="accepted",
                    accepted_at=successor_at,
                    time_difference_seconds=time_delta,
                    search_window_seconds=config["token_carryover_max_seconds"],
                    details={
                        "rule_id": token["rule_id"],
                        "ad_type": token["ad_type"],
                        "company_id": token.get("company_id"),
                        "service_uuid": token.get("service_uuid"),
                        "token_hash": token_hash,
                        "token_bit_length": token["bit_length"],
                        "predecessor_token_observations": token_evidence[predecessor.id][token_hash]["observation_count"],
                        "successor_token_observations": token["observation_count"],
                        "candidate_count": 1,
                        "acceptance_basis": "operator_approved_protocol_token_rule",
                    },
                )
                if correlation is not None:
                    db.flush()
                    _merge_accepted_identity_correlation(
                        db,
                        correlation,
                        predecessor_device,
                        successor_device,
                        observations_by_identity[successor.id],
                    )
                    accepted += 1
                break

    if not include_statistical_review:
        return {"accepted": accepted, "proposals": proposals}

    raw_pairs: list[tuple[ObservedIdentity, ObservedIdentity, LogicalDevice, LogicalDevice, float, float, int, int]] = []
    statistical_successors = (
        [identity for identity in identities if identity.id in eligible_successor_ids]
        if eligible_successor_ids is not None
        else identities
    )
    for successor in statistical_successors:
        if successor.id not in metadata or _accepted_correlation_for_successor(db, successor.id):
            continue
        successor_first, successor_last, successor_device = metadata[successor.id]
        successor_at = ensure_utc(successor_first.observed_at)
        successor_end = successor_at + timedelta(seconds=config["evaluation_window_seconds"])
        successor_points = _trusted_rssi_points(
            observations_by_identity[successor.id],
            successor_at,
            successor_end,
            successor_first.scanner_id,
        )
        if len(successor_points) < config["min_regression_samples"]:
            continue
        for predecessor in identities:
            if predecessor.id == successor.id or predecessor.id not in metadata:
                continue
            predecessor_first, predecessor_last, predecessor_device = metadata[predecessor.id]
            predecessor_at = ensure_utc(predecessor_last.observed_at)
            if predecessor_last.scanner_id != successor_first.scanner_id:
                continue
            time_delta = (successor_at - predecessor_at).total_seconds()
            if time_delta < 0 or time_delta > config["rotation_window_seconds"]:
                continue
            predecessor_start = predecessor_at - timedelta(seconds=config["evaluation_window_seconds"])
            predecessor_points = _trusted_rssi_points(
                observations_by_identity[predecessor.id],
                predecessor_start,
                predecessor_at,
                predecessor_last.scanner_id,
            )
            if len(predecessor_points) < config["min_regression_samples"]:
                continue
            difference = rssi_regression_difference(predecessor_points, successor_points)
            if difference is None:
                continue
            rho, predecessor_samples, successor_samples = difference
            raw_pairs.append(
                (
                    predecessor,
                    successor,
                    predecessor_device,
                    successor_device,
                    time_delta,
                    rho,
                    predecessor_samples,
                    successor_samples,
                )
            )

    if raw_pairs:
        alpha = config["alpha"] or alpha_from_p90_overlap(
            [pair[4] for pair in raw_pairs], [pair[5] for pair in raw_pairs]
        )
        if alpha is not None:
            pair_costs = {}
            pair_context: dict[tuple[str, str], tuple[ObservedIdentity, ObservedIdentity, LogicalDevice, LogicalDevice]] = {}
            for predecessor, successor, predecessor_device, successor_device, _, rho, predecessor_samples, successor_samples in raw_pairs:
                predecessor_last = metadata[predecessor.id][1]
                successor_first = metadata[successor.id][0]
                cost = akiyama_pair_cost(
                    ensure_utc(predecessor_last.observed_at),
                    ensure_utc(successor_first.observed_at),
                    rho,
                    alpha,
                    config["rotation_window_seconds"],
                    predecessor_samples,
                    successor_samples,
                )
                if cost is not None:
                    pair_costs[(predecessor.id, successor.id)] = cost
                    pair_context[(predecessor.id, successor.id)] = (
                        predecessor,
                        successor,
                        predecessor_device,
                        successor_device,
                    )
            predecessor_ids = sorted({key[0] for key in pair_costs})
            successor_ids = sorted({key[1] for key in pair_costs})
            for predecessor_id, successor_id, pair in assign_akiyama_pairs(
                predecessor_ids,
                successor_ids,
                pair_costs,
                config["unmatched_cost"],
            ):
                predecessor, successor, predecessor_device, successor_device = pair_context[(predecessor_id, successor_id)]
                if _correlation_exists(db, predecessor_id, successor_id, "akiyama_time_rssi_linear_assignment_v1"):
                    continue
                successor_at = ensure_utc(metadata[successor.id][0].observed_at)
                correlation = _record_identity_correlation(
                    db,
                    predecessor_identity=predecessor,
                    successor_identity=successor,
                    predecessor_device=predecessor_device,
                    successor_device=successor_device,
                    method="akiyama_time_rssi_linear_assignment_v1",
                    status="proposal",
                    accepted_at=None,
                    time_difference_seconds=pair.time_difference_seconds,
                    rssi_difference_db=pair.rssi_difference_db,
                    assignment_cost=pair.cost,
                    alpha=pair.alpha,
                    search_window_seconds=config["rotation_window_seconds"],
                    evaluation_window_seconds=config["evaluation_window_seconds"],
                    details={
                        "formula": "sqrt(tau^2 + (alpha * rho)^2)",
                        "rss_prediction": "linear_regression_from_predecessor_window",
                        "time_quality": "trusted_scanner_clock_only",
                        "scanner_id": metadata[successor.id][0].scanner_id,
                        "predecessor_sample_count": pair.predecessor_sample_count,
                        "successor_sample_count": pair.successor_sample_count,
                        "alpha_source": "configured" if config["alpha"] is not None else "per_run_p90_width_matching",
                        "unmatched_cost": config["unmatched_cost"],
                        "automatic_acceptance": False,
                    },
                )
                if correlation is None:
                    continue
                proposals += 1
    return {"accepted": accepted, "proposals": proposals}


def process_batch(db: Session, scanner: Scanner, payload: ObservationBatchIn) -> dict[str, Any]:
    received_at = utcnow()
    accepted = 0
    duplicates = 0
    ignored = 0
    errors = 0
    logical_device_ids: set[str] = set()
    correlation_candidate_identity_ids: set[str] = set()
    previous_status = scanner.status

    for obs in payload.observations:
        try:
            ok, result = process_observation(db, scanner, payload, obs, received_at)
            if ok:
                accepted += 1
                if result.get("logical_device_id"):
                    logical_device_ids.add(result["logical_device_id"])
                if result.get("observed_identity_id") and result.get("randomized_address"):
                    correlation_candidate_identity_ids.add(result["observed_identity_id"])
            elif result.get("duplicate"):
                duplicates += 1
            elif result.get("ignored"):
                ignored += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            db.add(
                ProcessingError(
                    scanner_id=scanner.id,
                    batch_id=payload.batch_id,
                    observation_id=obs.observation_id,
                    error_category=exc.__class__.__name__,
                    message=str(exc),
                    payload_excerpt=obs.model_dump(mode="json", exclude_none=True),
                )
            )

    scanner.last_seen_at = received_at
    scanner.status = "online"
    if previous_status != "online":
        create_event(
            db,
            "scanner_connected",
            scanner_id=scanner.id,
            occurred_at=received_at,
            previous_state=previous_status,
            new_state="online",
            confidence=0.9,
            reason="observation_batch_received",
            dedupe_key=f"scanner-connected:{scanner.id}:{received_at.strftime('%Y%m%d%H%M')}",
        )
    db.flush()
    try:
        correlations = (
            run_identity_correlation(
                db,
                include_statistical_review=True,
                successor_identity_ids=correlation_candidate_identity_ids,
            )
            if correlation_candidate_identity_ids
            else {"accepted": 0, "proposals": 0}
        )
    except Exception as exc:  # noqa: BLE001
        correlations = {"accepted": 0, "proposals": 0}
        errors += 1
        db.add(
            ProcessingError(
                scanner_id=scanner.id,
                batch_id=payload.batch_id,
                error_category="identity_correlation_error",
                message=str(exc),
            )
        )
    db.commit()
    return {
        "accepted": accepted,
        "duplicates": duplicates,
        "ignored": ignored,
        "errors": errors,
        "logical_device_ids": sorted(logical_device_ids),
        "identity_correlations": correlations,
    }


def refresh_presence_states(db: Session, settings: Settings) -> list[DeviceEvent]:
    now = utcnow()
    events: list[DeviceEvent] = []
    processing_settings = ProcessingSettings(
        presence_missing_seconds=settings.presence_missing_seconds,
        presence_offline_seconds=settings.presence_offline_seconds,
    )
    devices = db.execute(select(LogicalDevice).where(LogicalDevice.ignored.is_(False))).scalars().all()
    identity_bases = presence_identity_bases(db, devices)
    changed = False
    for device in devices:
        unresolved_random = identity_bases.get(device.id) == "unresolved_randomized_address"
        if unresolved_random and device.status == "identity_expired":
            continue
        if unresolved_random:
            missing_at = ensure_utc(device.last_seen_at) + timedelta(
                seconds=settings.presence_missing_seconds
            )
            result = (
                ("identity_expired", "randomized_address_not_correlated")
                if now >= missing_at
                else None
            )
        else:
            result = evaluate_presence_status(device.status, device.last_seen_at, now, processing_settings)
        if result is None:
            continue
        new_status, reason = result
        previous = device.status
        device.status = new_status
        changed = True
        event = create_event(
            db,
            f"device_{new_status}",
            now,
            scanner_id=device.current_scanner_id,
            logical_device_id=device.id,
            previous_state=previous,
            new_state=new_status,
            confidence=0.8,
            reason=reason,
            dedupe_key=f"presence:{device.id}:{new_status}:{now.strftime('%Y%m%d%H%M')}",
            severity="warning" if new_status == "offline" else "info",
        )
        if event is not None:
            events.append(event)
    if not changed:
        return []
    if not commit_allowing_event_dedupe_race(db):
        return []
    return events


def refresh_scanner_states(db: Session, settings: Settings) -> list[DeviceEvent]:
    now = utcnow()
    timeout_at = now - timedelta(seconds=settings.heartbeat_timeout_seconds)
    events: list[DeviceEvent] = []
    scanners = db.execute(select(Scanner).where(Scanner.enabled.is_(True))).scalars()
    changed = False
    for scanner in scanners:
        latest_signal_at = scanner.last_heartbeat_at
        if scanner.last_seen_at is not None:
            latest_signal_at = latest_datetime(latest_signal_at, scanner.last_seen_at) if latest_signal_at else scanner.last_seen_at
        if latest_signal_at and ensure_utc(latest_signal_at) < timeout_at and scanner.status != "offline":
            previous = scanner.status
            scanner.status = "offline"
            changed = True
            event = create_event(
                db,
                "scanner_disconnected",
                now,
                scanner_id=scanner.id,
                previous_state=previous,
                new_state="offline",
                confidence=1.0,
                reason="heartbeat_timeout",
                dedupe_key=f"scanner-offline:{scanner.id}:{now.strftime('%Y%m%d%H%M')}",
                severity="warning",
            )
            if event is not None:
                events.append(event)
    if not changed:
        return []
    if not commit_allowing_event_dedupe_race(db):
        return []
    return events


def serialize_scanner(scanner: Scanner) -> dict[str, Any]:
    return {
        "id": scanner.id,
        "display_name": scanner.display_name,
        "hardware_id": scanner.hardware_id,
        "installation_name": scanner.installation_name,
        "status": scanner.status,
        "enabled": scanner.enabled,
        "building": scanner.building,
        "floor": scanner.floor,
        "room": scanner.room,
        "zone": scanner.zone,
        "latitude": scanner.latitude,
        "longitude": scanner.longitude,
        "location_source": scanner.location_source,
        "location_observed_at": serialize_datetime(scanner.location_observed_at),
        "location_accuracy_m": scanner.location_accuracy_m,
        "indoor_x": scanner.indoor_x,
        "indoor_y": scanner.indoor_y,
        "orientation_deg": scanner.orientation_deg,
        "firmware_version": scanner.firmware_version,
        "hardware_version": scanner.hardware_version,
        "last_heartbeat_at": serialize_datetime(scanner.last_heartbeat_at),
        "last_seen_at": serialize_datetime(scanner.last_seen_at),
        "uptime_seconds": scanner.uptime_seconds,
        "reset_reason": scanner.reset_reason,
        "config_version": scanner.config_version,
        "network_info": scanner.network_info,
        "maintenance_notes": scanner.maintenance_notes,
    }


def proximity_model_from_estimate(estimate: DeviceLocationEstimate | None) -> dict[str, Any] | None:
    if estimate is None or not isinstance(estimate.details, dict):
        return None
    model = estimate.details.get("proximity_model")
    return model if isinstance(model, dict) else None


def proximity_model_from_observation(observation: Observation | None) -> dict[str, Any] | None:
    if observation is None or not isinstance(observation.processing_notes, dict):
        return None
    model = observation.processing_notes.get("proximity_model")
    return model if isinstance(model, dict) else None


def capture_provenance_from_observation(observation: Observation | None) -> dict[str, Any]:
    if observation is None or not isinstance(observation.processing_notes, dict):
        return {}
    capture = observation.processing_notes.get("capture_provenance")
    return capture if isinstance(capture, dict) else {}


def manufacturer_profile_from_observation(observation: Observation | None) -> dict[str, Any]:
    """Return company metadata only when raw AD parsing verified its source."""
    capture = capture_provenance_from_observation(observation)
    capture_status = capture.get("capture_status") or "capture_evidence_missing"
    if observation is not None and capture_status == "verified":
        profile = analyze_manufacturer_data(observation.manufacturer_data)
        return {**profile, "evidence": "raw_advertising_verified"}
    return {
        "company_id": None,
        "company_name": None,
        "evidence": capture_status,
    }


def device_classification_from_observation(
    observation: Observation | None,
    *,
    name: str | None,
    address: str | None,
    address_type: str | None,
    enrichment: DeviceEnrichment | None = None,
    observation_count: int | None = None,
) -> dict[str, Any] | None:
    capture = capture_provenance_from_observation(observation)
    return classify_flipper_zero(
        service_uuids=observation.service_uuids if observation else [],
        capture_verified=capture.get("capture_status") == "verified",
        name=name,
        address=address,
        address_type=address_type,
        tx_power=observation.tx_power if observation else None,
        advertising_type=observation.advertising_type if observation else None,
        connectable=observation.connectable if observation else None,
        advertising_flags=observation.advertising_flags if observation else None,
        gatt_manufacturer_name=enrichment.manufacturer_name if enrichment else None,
        gatt_services=enrichment.discovered_services if enrichment else [],
        observation_count=observation_count,
    )


def latest_location_estimates(db: Session, logical_device_ids: list[str]) -> dict[str, DeviceLocationEstimate]:
    if not logical_device_ids:
        return {}

    latest_timestamps = (
        select(
            DeviceLocationEstimate.logical_device_id.label("logical_device_id"),
            func.max(DeviceLocationEstimate.estimated_at).label("estimated_at"),
        )
        .where(DeviceLocationEstimate.logical_device_id.in_(logical_device_ids))
        .group_by(DeviceLocationEstimate.logical_device_id)
        .subquery()
    )
    rows = db.execute(
        select(DeviceLocationEstimate)
        .join(
            latest_timestamps,
            and_(
                DeviceLocationEstimate.logical_device_id == latest_timestamps.c.logical_device_id,
                DeviceLocationEstimate.estimated_at == latest_timestamps.c.estimated_at,
            ),
        )
        .order_by(DeviceLocationEstimate.logical_device_id, desc(DeviceLocationEstimate.id))
    ).scalars()
    latest: dict[str, DeviceLocationEstimate] = {}
    for row in rows:
        # Multiple rows can share a timestamp; preserve one stable primary-key
        # tie-breaker without materializing the complete estimate history.
        latest.setdefault(row.logical_device_id, row)
    return latest


def latest_observations(db: Session, logical_device_ids: list[str]) -> dict[str, Observation]:
    if not logical_device_ids:
        return {}

    latest_timestamps = (
        select(
            Observation.logical_device_id.label("logical_device_id"),
            func.max(Observation.observed_at).label("observed_at"),
        )
        .where(Observation.logical_device_id.in_(logical_device_ids))
        .group_by(Observation.logical_device_id)
        .subquery()
    )
    rows = db.execute(
        select(Observation)
        .join(
            latest_timestamps,
            and_(
                Observation.logical_device_id == latest_timestamps.c.logical_device_id,
                Observation.observed_at == latest_timestamps.c.observed_at,
            ),
        )
        .order_by(Observation.logical_device_id, desc(Observation.id))
    ).scalars()
    latest: dict[str, Observation] = {}
    for row in rows:
        latest.setdefault(row.logical_device_id, row)
    return latest


def latest_device_enrichments(db: Session, logical_device_ids: list[str]) -> dict[str, DeviceEnrichment]:
    if not logical_device_ids:
        return {}

    rows = db.execute(
        select(DeviceEnrichment)
        .where(DeviceEnrichment.logical_device_id.in_(logical_device_ids))
        .order_by(DeviceEnrichment.logical_device_id, desc(DeviceEnrichment.enriched_at), desc(DeviceEnrichment.id))
    ).scalars()
    latest: dict[str, DeviceEnrichment] = {}
    for row in rows:
        latest.setdefault(row.logical_device_id, row)
    return latest


def serialize_device_enrichment(enrichment: DeviceEnrichment) -> dict[str, Any]:
    return {
        "id": enrichment.id,
        "scanner_id": enrichment.scanner_id,
        "observed_identity_id": enrichment.observed_identity_id,
        "source_observation_id": enrichment.source_observation_id,
        "enriched_at": serialize_datetime(enrichment.enriched_at),
        "transport": enrichment.transport,
        "status": enrichment.status,
        "device_name": enrichment.device_name,
        "manufacturer_name": enrichment.manufacturer_name,
        "model_number": enrichment.model_number,
        "serial_number": enrichment.serial_number,
        "firmware_revision": enrichment.firmware_revision,
        "hardware_revision": enrichment.hardware_revision,
        "software_revision": enrichment.software_revision,
        "system_id": enrichment.system_id,
        "pnp_id": enrichment.pnp_id,
        "discovered_services": enrichment.discovered_services,
        "characteristic_values": enrichment.characteristic_values,
        "error_code": enrichment.error_code,
        "attempt_duration_ms": enrichment.attempt_duration_ms,
        "details": enrichment.details,
    }


def serialize_device(
    device: LogicalDevice,
    latest_location: DeviceLocationEstimate | None = None,
    latest_observation: Observation | None = None,
    latest_enrichment: DeviceEnrichment | None = None,
    identity_basis: str | None = None,
    visibility_class: str | None = None,
) -> dict[str, Any]:
    proximity_model = (
        proximity_model_from_observation(latest_observation)
        or proximity_model_from_estimate(latest_location)
    )
    location_details = (
        latest_location.details
        if latest_location is not None and isinstance(latest_location.details, dict)
        else {}
    )
    manufacturer_profile = manufacturer_profile_from_observation(latest_observation)
    address_type = (device.primary_address_type or "").lower()
    identity_basis = identity_basis or (
        "unresolved_randomized_address"
        if any(token in address_type for token in ("random", "private", "rpa"))
        else "observed_stable_address"
    )
    visibility_class = visibility_class or (
        "transient_broadcast"
        if identity_basis == "unresolved_randomized_address"
        else "device_candidate"
    )
    resolved_name = device.alias or (latest_enrichment.device_name if latest_enrichment else None) or device.display_name
    name_source = (
        "operator_alias"
        if device.alias
        else "ble_gatt_device_name"
        if latest_enrichment and latest_enrichment.device_name
        else "advertising_local_name_or_address"
    )
    device_classification = device_classification_from_observation(
        latest_observation,
        name=device.display_name,
        address=device.primary_address,
        address_type=device.primary_address_type,
        enrichment=latest_enrichment,
        observation_count=device.observation_count,
    )
    return {
        "id": device.id,
        "alias": device.alias,
        "display_name": resolved_name,
        "display_name_source": name_source,
        "primary_address": device.primary_address,
        "address_type": device.primary_address_type,
        "vendor": manufacturer_profile["company_name"],
        "manufacturer_company_id": manufacturer_profile["company_id"],
        "manufacturer_evidence": manufacturer_profile["evidence"],
        "category": device_classification["product_class"] if device_classification else device.category,
        "device_classification": device_classification,
        "status": "ignored" if device.ignored else device.status,
        "movement_status": device.movement_status,
        "known": device.known,
        "ignored": device.ignored,
        "identity_confidence": device.identity_confidence,
        "identity_basis": identity_basis,
        "presence_trackable": identity_basis != "unresolved_randomized_address",
        "visibility_class": visibility_class,
        "location_confidence": device.location_confidence,
        "movement_confidence": device.movement_confidence,
        "current_scanner_id": device.current_scanner_id,
        "current_zone": device.current_zone,
        "proximity_band": device.proximity_band,
        "estimated_distance_m": device.estimated_distance_m,
        "distance_range_m": proximity_model.get("distance_range_m") if proximity_model else None,
        "proximity_confidence": proximity_model.get("confidence") if proximity_model else device.location_confidence,
        "proximity_model": proximity_model,
        "smoothed_rssi": device.smoothed_rssi,
        "latitude": device.latitude,
        "longitude": device.longitude,
        "location_anchor": {
            "scanner_id": device.current_scanner_id,
            "zone": device.current_zone,
            "latitude": device.latitude,
            "longitude": device.longitude,
            "anchored_at": serialize_datetime(device.location_anchor_observed_at),
            "source": "scanner_snapshot_at_observation",
            "scanner_location_source": location_details.get("scanner_location_source"),
            "scanner_location_observed_at": location_details.get("scanner_location_observed_at"),
            "accuracy_m": location_details.get("scanner_location_accuracy_m"),
            "update_policy": "latest_observation_with_current_scanner_position",
        },
        "first_seen_at": serialize_datetime(device.first_seen_at),
        "last_seen_at": serialize_datetime(device.last_seen_at),
        "observation_count": device.observation_count,
        "notes": device.notes,
        "tags": device.tags,
        "gatt_enrichment": serialize_device_enrichment(latest_enrichment) if latest_enrichment else None,
    }


def serialize_recent_observation(observation: Observation) -> dict[str, Any]:
    proximity_model = proximity_model_from_observation(observation)
    processing_notes = observation.processing_notes if isinstance(observation.processing_notes, dict) else {}
    return {
        "id": observation.id,
        "observed_at": serialize_datetime(observation.observed_at),
        "scanner_time": serialize_datetime(observation.scanner_time),
        "server_received_at": serialize_datetime(observation.server_received_at),
        "scanner_id": observation.scanner_id,
        "rssi": observation.rssi,
        "tx_power": observation.tx_power,
        "estimated_distance_m": observation.estimated_distance_m,
        "proximity_model": proximity_model,
        "proximity_band": proximity_model.get("band") if proximity_model else proximity_band(observation.estimated_distance_m, observation.rssi),
        "raw_advertising_payload": observation.raw_advertising_payload,
        "raw_scan_response_payload": observation.raw_scan_response_payload,
        "capture_provenance": processing_notes.get("capture_provenance"),
        "time_provenance": processing_notes.get("time_provenance"),
    }


def serialize_event(event: DeviceEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "severity": event.severity,
        "scanner_id": event.scanner_id,
        "logical_device_id": event.logical_device_id,
        "observed_identity_id": event.observed_identity_id,
        "occurred_at": serialize_datetime(event.occurred_at),
        "previous_state": event.previous_state,
        "new_state": event.new_state,
        "previous_location": event.previous_location,
        "new_location": event.new_location,
        "confidence": event.confidence,
        "reason": event.reason,
        "details": event.details,
    }


def serialize_identity_correlation(correlation: DeviceIdentityCorrelation) -> dict[str, Any]:
    return {
        "id": correlation.id,
        "predecessor_identity_id": correlation.predecessor_identity_id,
        "successor_identity_id": correlation.successor_identity_id,
        "predecessor_logical_device_id": correlation.predecessor_logical_device_id,
        "successor_logical_device_id": correlation.successor_logical_device_id,
        "method": correlation.method,
        "status": correlation.status,
        "time_difference_seconds": correlation.time_difference_seconds,
        "rssi_difference_db": correlation.rssi_difference_db,
        "assignment_cost": correlation.assignment_cost,
        "alpha": correlation.alpha,
        "search_window_seconds": correlation.search_window_seconds,
        "evaluation_window_seconds": correlation.evaluation_window_seconds,
        "details": correlation.details,
        "created_at": serialize_datetime(correlation.created_at),
        "accepted_at": serialize_datetime(correlation.accepted_at),
    }


def serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return ensure_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def presence_identity_bases(db: Session, devices: list[LogicalDevice]) -> dict[str, str]:
    """Separate durable presence identities from unresolved random addresses."""

    if not devices:
        return {}
    device_ids = [device.id for device in devices]
    correlated_ids = set(
        db.execute(
            select(Observation.logical_device_id)
            .where(Observation.logical_device_id.in_(device_ids))
            .group_by(Observation.logical_device_id)
            .having(func.count(func.distinct(Observation.observed_identity_id)) > 1)
        ).scalars()
    )
    gatt_stable_ids = set(
        db.execute(
            select(DeviceEnrichment.logical_device_id)
            .where(
                DeviceEnrichment.logical_device_id.in_(device_ids),
                DeviceEnrichment.status.in_(("success", "partial")),
                or_(
                    DeviceEnrichment.serial_number.is_not(None),
                    DeviceEnrichment.system_id.is_not(None),
                ),
            )
            .distinct()
        ).scalars()
    )

    bases: dict[str, str] = {}
    for device in devices:
        address_type = (device.primary_address_type or "").lower()
        randomized = any(token in address_type for token in ("random", "private", "rpa"))
        if device.known or bool(device.alias):
            bases[device.id] = "operator_confirmed_identity"
        elif not randomized:
            bases[device.id] = "observed_stable_address"
        elif device.id in gatt_stable_ids:
            bases[device.id] = "gatt_stable_identifier"
        elif device.id in correlated_ids:
            bases[device.id] = "correlated_randomized_identity"
        else:
            bases[device.id] = "unresolved_randomized_address"
    return bases


def device_visibility_classes(
    db: Session,
    devices: list[LogicalDevice],
    identity_bases: dict[str, str],
) -> dict[str, str]:
    """Promote direct named broadcasts without treating them as durable identity."""
    if not devices:
        return {}
    device_ids = [device.id for device in devices]
    named_broadcast_ids = set(
        db.execute(
            select(Observation.logical_device_id)
            .join(
                ObservedIdentity,
                ObservedIdentity.id == Observation.observed_identity_id,
            )
            .where(
                Observation.logical_device_id.in_(device_ids),
                or_(
                    and_(
                        ObservedIdentity.local_name.is_not(None),
                        ObservedIdentity.local_name != "",
                    ),
                    and_(
                        ObservedIdentity.advertised_name.is_not(None),
                        ObservedIdentity.advertised_name != "",
                    ),
                ),
            )
            .distinct()
        ).scalars()
    )
    return {
        device.id: (
            "device_candidate"
            if identity_bases.get(device.id) != "unresolved_randomized_address"
            else "named_broadcast_candidate"
            if device.id in named_broadcast_ids
            else "transient_broadcast"
        )
        for device in devices
    }


def overview(db: Session) -> dict[str, Any]:
    scanners = db.execute(select(Scanner)).scalars().all()
    devices = db.execute(select(LogicalDevice)).scalars().all()
    identity_bases = presence_identity_bases(db, devices)
    visibility_classes = device_visibility_classes(db, devices, identity_bases)
    present_statuses = {"active", "newly_detected", "returned"}
    trackable_devices = [
        device for device in devices if identity_bases.get(device.id) != "unresolved_randomized_address"
    ]
    unresolved_devices = [
        device for device in devices if identity_bases.get(device.id) == "unresolved_randomized_address"
    ]
    recent_since = utcnow() - timedelta(minutes=1)
    recent_observations = db.execute(select(func.count(Observation.id)).where(Observation.server_received_at >= recent_since)).scalar_one()
    recent_events = db.execute(select(DeviceEvent).order_by(desc(DeviceEvent.occurred_at)).limit(10)).scalars().all()

    return {
        "scanner_total": len(scanners),
        "scanner_online": sum(1 for scanner in scanners if scanner.status == "online"),
        "scanner_offline": sum(1 for scanner in scanners if scanner.status == "offline"),
        "present_ble_records": sum(1 for device in devices if device.status in present_statuses),
        "active_devices": sum(
            1 for device in trackable_devices if device.status in present_statuses
        ),
        "active_unresolved_identities": sum(
            1 for device in unresolved_devices if device.status in present_statuses
        ),
        "visible_device_candidates": sum(
            1
            for device in devices
            if device.status in present_statuses
            and visibility_classes.get(device.id) != "transient_broadcast"
        ),
        "newly_detected_devices": sum(1 for device in trackable_devices if device.status == "newly_detected"),
        "moving_devices": sum(
            1
            for device in trackable_devices
            if device.movement_status in {"probably_moving", "relocated_between_scanners"}
        ),
        "stationary_devices": sum(1 for device in trackable_devices if device.movement_status == "signal_stable"),
        "missing_devices": sum(1 for device in trackable_devices if device.status == "temporarily_missing"),
        "offline_device_records": sum(
            1
            for device in devices
            if device.status == "offline" and identity_bases.get(device.id) != "unresolved_randomized_address"
        ),
        "expired_random_identities": sum(
            1
            for device in devices
            if device.status == "identity_expired"
            or (device.status == "offline" and identity_bases.get(device.id) == "unresolved_randomized_address")
        ),
        "ignored_devices": sum(1 for device in devices if device.ignored),
        "observation_rate_per_minute": recent_observations,
        "system_health": "ok" if any(scanner.status == "online" for scanner in scanners) or not scanners else "warning",
        "recent_events": [serialize_event(event) for event in recent_events],
    }


def list_devices(
    db: Session,
    status: str | None = None,
    scanner_id: str | None = None,
    include_ignored: bool = False,
    include_expired: bool = False,
    include_transient: bool = True,
) -> list[dict[str, Any]]:
    query = select(LogicalDevice).order_by(desc(LogicalDevice.last_seen_at))
    if status == "present":
        query = query.where(LogicalDevice.status.in_(("active", "newly_detected", "returned")))
    elif status:
        query = query.where(LogicalDevice.status == status)
    if scanner_id:
        query = query.where(LogicalDevice.current_scanner_id == scanner_id)
    if not include_ignored:
        query = query.where(LogicalDevice.ignored.is_(False))
    if not include_expired and status != "identity_expired":
        query = query.where(LogicalDevice.status != "identity_expired")
    devices = db.execute(query).scalars().all()
    identity_bases = presence_identity_bases(db, devices)
    visibility_classes = device_visibility_classes(db, devices, identity_bases)
    if not include_transient:
        devices = [
            device
            for device in devices
            if visibility_classes.get(device.id) != "transient_broadcast"
        ]
    latest = latest_location_estimates(db, [device.id for device in devices])
    observations = latest_observations(db, [device.id for device in devices])
    enrichments = latest_device_enrichments(db, [device.id for device in devices])
    return [
        serialize_device(
            device,
            latest.get(device.id),
            observations.get(device.id),
            enrichments.get(device.id),
            identity_bases.get(device.id),
            visibility_classes.get(device.id),
        )
        for device in devices
    ]


def device_detail(db: Session, device_id: str) -> dict[str, Any] | None:
    device = db.get(LogicalDevice, device_id)
    if device is None:
        return None
    observations = db.execute(
        select(Observation).where(Observation.logical_device_id == device_id).order_by(desc(Observation.observed_at)).limit(100)
    ).scalars().all()
    events = db.execute(
        select(DeviceEvent).where(DeviceEvent.logical_device_id == device_id).order_by(desc(DeviceEvent.occurred_at)).limit(100)
    ).scalars().all()
    identities = db.execute(
        select(ObservedIdentity)
        .join(Observation, Observation.observed_identity_id == ObservedIdentity.id)
        .where(Observation.logical_device_id == device_id)
        .distinct()
    ).scalars().all()
    identity_ids = [identity.id for identity in identities]
    correlations = []
    if identity_ids:
        correlations = db.execute(
            select(DeviceIdentityCorrelation)
            .where(
                or_(
                    DeviceIdentityCorrelation.predecessor_identity_id.in_(identity_ids),
                    DeviceIdentityCorrelation.successor_identity_id.in_(identity_ids),
                )
            )
            .order_by(desc(DeviceIdentityCorrelation.created_at))
            .limit(100)
        ).scalars().all()
    location_history = db.execute(
        select(DeviceLocationEstimate)
        .where(DeviceLocationEstimate.logical_device_id == device_id)
        .order_by(desc(DeviceLocationEstimate.estimated_at))
        .limit(100)
    ).scalars().all()
    enrichments = db.execute(
        select(DeviceEnrichment)
        .where(DeviceEnrichment.logical_device_id == device_id)
        .order_by(desc(DeviceEnrichment.enriched_at))
        .limit(100)
    ).scalars().all()
    latest = latest_location_estimates(db, [device_id])
    latest_by_identity: dict[str, Observation] = {}
    for observation in observations:
        latest_by_identity.setdefault(observation.observed_identity_id, observation)
    latest_enrichment_by_identity: dict[str, DeviceEnrichment] = {}
    for enrichment in enrichments:
        if enrichment.observed_identity_id:
            latest_enrichment_by_identity.setdefault(enrichment.observed_identity_id, enrichment)
    observed_identities = []
    for identity in identities:
        latest_identity_observation = latest_by_identity.get(identity.id)
        manufacturer_profile = manufacturer_profile_from_observation(latest_identity_observation)
        device_classification = device_classification_from_observation(
            latest_identity_observation,
            name=identity.local_name or identity.advertised_name,
            address=identity.address,
            address_type=identity.address_type,
            enrichment=latest_enrichment_by_identity.get(identity.id),
            observation_count=identity.observation_count,
        )
        observed_identities.append(
            {
                "id": identity.id,
                "address": identity.address,
                "address_type": identity.address_type,
                "advertised_name": identity.advertised_name,
                "local_name": identity.local_name,
                "service_uuids": identity.service_uuids,
                "service_data": identity.service_data,
                "manufacturer_data": identity.manufacturer_data,
                "appearance": identity.appearance,
                "advertising_flags": identity.advertising_flags,
                "manufacturer_profile": manufacturer_profile,
                "manufacturer_evidence": manufacturer_profile["evidence"],
                "device_classification": device_classification,
                "randomized_address": identity.randomized_address,
                "first_seen_at": serialize_datetime(identity.first_seen_at),
                "last_seen_at": serialize_datetime(identity.last_seen_at),
                "observation_count": identity.observation_count,
            }
        )
    identity_basis = presence_identity_bases(db, [device]).get(device.id)
    visibility_class = device_visibility_classes(
        db,
        [device],
        {device.id: identity_basis},
    ).get(device.id)
    return {
        "device": serialize_device(
            device,
            latest.get(device_id),
            observations[0] if observations else None,
            enrichments[0] if enrichments else None,
            identity_basis,
            visibility_class,
        ),
        "observed_identities": observed_identities,
        "recent_observations": [serialize_recent_observation(observation) for observation in observations],
        "location_history": [
            {
                "estimated_at": serialize_datetime(estimate.estimated_at),
                "scanner_id": estimate.scanner_id,
                "zone": estimate.zone,
                "proximity_band": estimate.proximity_band,
                "estimated_distance_m": estimate.estimated_distance_m,
                "confidence": estimate.confidence,
                "method": estimate.method,
                "details": estimate.details,
            }
            for estimate in location_history
        ],
        "events": [serialize_event(event) for event in events],
        "identity_correlations": [serialize_identity_correlation(correlation) for correlation in correlations],
        "device_enrichments": [serialize_device_enrichment(enrichment) for enrichment in enrichments],
    }


def list_scanners(db: Session) -> list[dict[str, Any]]:
    scanners = db.execute(select(Scanner).order_by(Scanner.display_name)).scalars().all()
    return [serialize_scanner(scanner) for scanner in scanners]


def patch_scanner(db: Session, scanner_id: str, payload: ScannerPatchIn) -> dict[str, Any] | None:
    scanner = db.get(Scanner, scanner_id)
    if scanner is None:
        return None
    updates = payload.model_dump(exclude_unset=True)
    coordinates_supplied = "latitude" in updates or "longitude" in updates
    for field, value in updates.items():
        setattr(scanner, field, value)
    if coordinates_supplied:
        scanner.location_source = "configured"
        scanner.location_observed_at = utcnow()
        scanner.location_accuracy_m = None
    scanner.config_version += 1
    config = get_scanner_config(db, scanner_id)
    config.version = scanner.config_version
    db.commit()
    db.refresh(scanner)
    return serialize_scanner(scanner)


def record_scanner_position(
    db: Session,
    scanner_id: str,
    payload: ScannerPositionIn,
) -> dict[str, Any] | None:
    scanner = db.get(Scanner, scanner_id)
    if scanner is None:
        return None

    observed_at = ensure_utc(payload.observed_at)
    if observed_at > utcnow() + timedelta(minutes=1):
        raise ValueError("scanner position timestamp is too far in the future")

    current_observed_at = (
        ensure_utc(scanner.location_observed_at)
        if scanner.location_observed_at is not None
        else None
    )
    applied = current_observed_at is None or observed_at > current_observed_at
    if applied:
        scanner.latitude = payload.latitude
        scanner.longitude = payload.longitude
        scanner.location_source = payload.source
        scanner.location_observed_at = observed_at
        scanner.location_accuracy_m = payload.accuracy_m
        db.commit()
        db.refresh(scanner)

    result = serialize_scanner(scanner)
    result["position_applied"] = applied
    return result


def list_events(db: Session, event_type: str | None = None, scanner_id: str | None = None, device_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    query = select(DeviceEvent).order_by(desc(DeviceEvent.occurred_at)).limit(min(max(limit, 1), 500))
    if event_type:
        query = query.where(DeviceEvent.event_type == event_type)
    if scanner_id:
        query = query.where(DeviceEvent.scanner_id == scanner_id)
    if device_id:
        query = query.where(DeviceEvent.logical_device_id == device_id)
    return [serialize_event(event) for event in db.execute(query).scalars().all()]


def get_settings_values(db: Session) -> dict[str, Any]:
    ensure_default_settings(db)
    rows = db.execute(select(SystemSetting).order_by(SystemSetting.key)).scalars().all()
    return {row.key: {"value": row.value, "description": row.description, "updated_at": serialize_datetime(row.updated_at)} for row in rows}


def patch_settings(db: Session, payload: SettingsPatchIn) -> dict[str, Any]:
    ensure_default_settings(db)
    for key, value in payload.values.items():
        if key not in DEFAULT_SETTINGS:
            raise ValueError(f"unsupported dynamic setting: {key}")
        value = validated_dynamic_setting(key, value)
        setting = db.get(SystemSetting, key)
        if setting is None:
            setting = SystemSetting(
                key=key,
                value=value,
                description=DEFAULT_SETTINGS[key][1],
            )
            db.add(setting)
        else:
            setting.value = value
    db.commit()
    return get_settings_values(db)


def validated_dynamic_setting(key: str, value: Any) -> Any:
    integer_ranges = {
        "correlation_rotation_window_seconds": (1, 3600),
        "correlation_evaluation_window_seconds": (1, 3600),
        "correlation_min_regression_samples": (2, 100),
        "correlation_token_carryover_max_seconds": (1, 604_800),
        "correlation_token_min_observations": (1, 100),
    }
    numeric_ranges = {
        "correlation_unmatched_cost_seconds": (0.001, 86_400.0),
    }
    if key in integer_ranges:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be an integer")
        minimum, maximum = integer_ranges[key]
        if not minimum <= value <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
        return value
    if key in numeric_ranges:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be numeric")
        minimum, maximum = numeric_ranges[key]
        if not minimum <= float(value) <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
        return float(value)
    if key == "correlation_alpha":
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("correlation_alpha must be numeric or null")
        if not 0.000001 <= float(value) <= 10_000.0:
            raise ValueError("correlation_alpha must be between 0.000001 and 10000")
        return float(value)
    if key == "correlation_token_rules":
        if not isinstance(value, list) or len(parse_token_rules(value)) != len(value):
            raise ValueError("correlation_token_rules contains an invalid token rule")
        return value
    raise ValueError(f"unsupported dynamic setting: {key}")


def diagnostics(db: Session) -> dict[str, Any]:
    now = utcnow()
    invalid_payload_count = db.execute(select(func.count(ProcessingError.id))).scalar_one()
    observation_count = db.execute(select(func.count(Observation.id))).scalar_one()
    event_count = db.execute(select(func.count(DeviceEvent.id))).scalar_one()
    observed_identity_count = db.execute(select(func.count(ObservedIdentity.id))).scalar_one()
    randomized_identity_count = db.execute(
        select(func.count(ObservedIdentity.id)).where(ObservedIdentity.randomized_address.is_(True))
    ).scalar_one()
    logical_device_count = db.execute(select(func.count(LogicalDevice.id))).scalar_one()
    identity_correlation_count = db.execute(select(func.count(DeviceIdentityCorrelation.id))).scalar_one()
    tracking_session_count = db.execute(select(func.count(DeviceTrackingSession.id))).scalar_one()
    tracking_sample_count = db.execute(select(func.count(DeviceTrackingSample.id))).scalar_one()
    scanners = db.execute(select(Scanner).order_by(Scanner.id)).scalars().all()
    latest_heartbeat = db.execute(select(ScannerHeartbeat).order_by(desc(ScannerHeartbeat.received_at)).limit(1)).scalar_one_or_none()
    recent_errors = db.execute(select(ProcessingError).order_by(desc(ProcessingError.created_at)).limit(20)).scalars()
    return {
        "server": {"status": "ok", "time": serialize_datetime(utcnow())},
        "database": {"status": "ok"},
        "processing": {
            "invalid_payload_count": invalid_payload_count,
            "observation_count": observation_count,
            "event_count": event_count,
            "tracking_session_count": tracking_session_count,
            "tracking_sample_count": tracking_sample_count,
        },
        "identity_counts": {
            "observed_identities": observed_identity_count,
            "randomized_address_identities": randomized_identity_count,
            "logical_devices": logical_device_count,
            "identity_correlations": identity_correlation_count,
        },
        "latest_heartbeat": {
            "scanner_id": latest_heartbeat.scanner_id,
            "received_at": serialize_datetime(latest_heartbeat.received_at),
            "buffer_usage": latest_heartbeat.buffer_usage,
            "pending_observations": latest_heartbeat.pending_observations,
            "dropped_observations": latest_heartbeat.dropped_observations,
            "firmware_version": latest_heartbeat.firmware_version,
            "uptime_seconds": latest_heartbeat.uptime_seconds,
            "health": latest_heartbeat.health or {},
        }
        if latest_heartbeat
        else None,
        "scanner_positions": [
            {
                "scanner_id": scanner.id,
                "coordinates_available": scanner_position_is_available(scanner),
                "source": scanner.location_source,
                "observed_at": serialize_datetime(scanner.location_observed_at),
                "age_seconds": (
                    max(
                        0.0,
                        (now - ensure_utc(scanner.location_observed_at)).total_seconds(),
                    )
                    if scanner.location_observed_at is not None
                    else None
                ),
                "accuracy_m": scanner.location_accuracy_m,
            }
            for scanner in scanners
        ],
        "recent_errors": [
            {
                "scanner_id": error.scanner_id,
                "batch_id": error.batch_id,
                "observation_id": error.observation_id,
                "error_category": error.error_category,
                "message": error.message,
                "created_at": serialize_datetime(error.created_at),
            }
            for error in recent_errors
        ],
    }


def patch_device_metadata(
    db: Session,
    device_id: str,
    payload: DevicePatchIn,
) -> dict[str, Any] | None:
    device = db.get(LogicalDevice, device_id)
    if device is None:
        return None
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        detail = device_detail(db, device_id)
        return detail["device"] if detail else None
    for field, value in updates.items():
        setattr(device, field, value)
    create_event(
        db,
        "manual_device_metadata_updated",
        utcnow(),
        scanner_id=device.current_scanner_id,
        logical_device_id=device.id,
        confidence=1.0,
        reason="operator_update",
        details={"updated_fields": sorted(updates)},
        dedupe_key=f"manual-device-metadata:{device.id}:{uuid.uuid4()}",
    )
    db.commit()
    detail = device_detail(db, device_id)
    return detail["device"] if detail else None


def apply_manual_correlation(
    db: Session,
    payload: ManualCorrelationIn,
    settings: Settings,
) -> dict[str, Any]:
    source = db.get(LogicalDevice, payload.source_logical_device_id)
    if source is None:
        raise ValueError("source logical device not found")

    result_device = source
    target_id = payload.target_logical_device_id
    observed_identity_id = payload.observed_identity_id
    correlation_id = payload.correlation_id
    result: dict[str, Any] = {"accepted": True}
    if payload.action == "mark_known":
        source.known = True
    elif payload.action == "mark_ignored":
        source.ignored = True
    elif payload.action == "unignore":
        source.ignored = False
        if source.status == "ignored":
            age = max(0.0, (utcnow() - ensure_utc(source.last_seen_at)).total_seconds())
            randomized = is_randomized_address(source.primary_address_type, source.primary_address)
            if randomized and age >= settings.presence_missing_seconds:
                source.status = "identity_expired"
            elif age >= settings.presence_offline_seconds:
                source.status = "offline"
            elif age >= settings.presence_missing_seconds:
                source.status = "temporarily_missing"
            else:
                source.status = "active"
    elif payload.action == "merge":
        target = db.get(LogicalDevice, payload.target_logical_device_id)
        if target is None:
            raise ValueError("target logical device not found")
        merge_result = merge_logical_devices(
            db,
            canonical=target,
            merged=source,
        )
        result_device = merge_result.canonical_device
        result["merged_device_id"] = source.id
        result["canonical_device_id"] = target.id
    elif payload.action == "split":
        identity = db.get(ObservedIdentity, payload.observed_identity_id)
        if identity is None:
            raise ValueError("observed identity not found")
        target = (
            db.get(LogicalDevice, payload.target_logical_device_id)
            if payload.target_logical_device_id
            else None
        )
        if payload.target_logical_device_id and target is None:
            raise ValueError("split target logical device not found")
        split_result = split_logical_identity(
            db,
            source=source,
            observed_identity=identity,
            settings=settings,
            target=target,
        )
        result_device = split_result.split_device
        target_id = split_result.split_device.id
        result["source_device_id"] = source.id
        result["split_device_id"] = split_result.split_device.id
        result["moved_observation_count"] = split_result.moved_observation_count
    elif payload.action in {"accept_proposal", "reject_proposal"}:
        correlation = db.get(DeviceIdentityCorrelation, payload.correlation_id)
        if correlation is None:
            raise ValueError("identity correlation proposal not found")
        if correlation.status != "proposal":
            raise ValueError("identity correlation has already been reviewed")
        if correlation.successor_logical_device_id != source.id:
            raise ValueError("proposal does not belong to the source logical device")
        predecessor = db.get(LogicalDevice, correlation.predecessor_logical_device_id)
        successor = db.get(LogicalDevice, correlation.successor_logical_device_id)
        if predecessor is None or successor is None:
            raise ValueError("proposal references a missing logical device")
        target_id = predecessor.id
        observed_identity_id = correlation.successor_identity_id
        details = dict(correlation.details or {})
        details["operator_review"] = {
            "decision": payload.action,
            "reason": payload.reason or "operator_decision",
            "reviewed_at": serialize_datetime(utcnow()),
        }
        correlation.details = details
        if payload.action == "accept_proposal":
            correlation.status = "accepted"
            correlation.accepted_at = utcnow()
            successor_observations = _identity_observations(
                db,
                correlation.successor_identity_id,
            )
            _merge_accepted_identity_correlation(
                db,
                correlation,
                predecessor,
                successor,
                successor_observations,
            )
            result_device = predecessor
            result["canonical_device_id"] = predecessor.id
            result["merged_device_id"] = successor.id
        else:
            correlation.status = "rejected"
            result_device = source
        result["correlation_id"] = correlation.id
        result["correlation_status"] = correlation.status

    decision = ManualDeviceCorrelationDecision(
        action=payload.action,
        source_logical_device_id=source.id,
        target_logical_device_id=target_id,
        observed_identity_id=observed_identity_id,
        correlation_id=correlation_id,
        reason=payload.reason,
    )
    db.add(decision)

    create_event(
        db,
        f"manual_device_{payload.action}",
        utcnow(),
        scanner_id=result_device.current_scanner_id,
        logical_device_id=result_device.id,
        observed_identity_id=observed_identity_id,
        confidence=1.0,
        reason=payload.reason or "operator_decision",
        details={
            **payload.model_dump(exclude_none=True),
            "result_device_id": result_device.id,
        },
        dedupe_key=f"manual:{payload.action}:{uuid.uuid4()}",
    )
    db.commit()
    detail = device_detail(db, result_device.id)
    result["device"] = detail["device"] if detail else serialize_device(result_device)
    return result
