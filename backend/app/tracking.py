from __future__ import annotations

from datetime import datetime, timedelta
from math import ceil
from statistics import median
from typing import Any

from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from .models import (
    DeviceTrackingPosition,
    DeviceTrackingSample,
    DeviceTrackingScanner,
    DeviceTrackingSession,
    LogicalDevice,
    Observation,
    ObservedIdentity,
    Scanner,
    ScannerConfiguration,
    SystemSetting,
)
from .processing import ensure_utc, utcnow
from .schemas import TrackingPositionIn, TrackingSampleBatchIn, TrackingSessionCreateIn
from .services import create_event, serialize_datetime


TRACKING_LEASE_SECONDS = 30
TRACKING_MEDIAN_WINDOW_SECONDS = 4
TRACKING_TREND_CURRENT_SECONDS = 4
TRACKING_TREND_PREVIOUS_SECONDS = 12
TRACKING_STALE_MIN_SECONDS = 12
TRACKING_STALE_MAX_SECONDS = 30
TRACKING_STALE_HISTORY_LIMIT = 60
TRACKING_SIGNAL_FAR_RSSI = -85.0
TRACKING_SIGNAL_NEAR_RSSI = -45.0
TRACKING_TARGET_LIMIT = 8
ACTIVE_SESSION_STATES = {
    "arming",
    "waiting_for_advertisement",
    "live",
    "stale",
    "scanner_offline",
    "identity_changed",
}
ACTIVE_ASSIGNMENT_STATES = ACTIVE_SESSION_STATES


class TrackingNotFoundError(ValueError):
    pass


class TrackingConflictError(ValueError):
    pass


class TrackingValidationError(ValueError):
    pass


def normalize_address(value: str) -> str:
    return value.strip().lower().replace("-", ":")


def signal_level(smoothed_rssi: float) -> float:
    span = TRACKING_SIGNAL_NEAR_RSSI - TRACKING_SIGNAL_FAR_RSSI
    return max(0.0, min(1.0, (smoothed_rssi - TRACKING_SIGNAL_FAR_RSSI) / span))


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = max(0.0, min(1.0, percentile)) * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def tracking_stale_seconds(db: Session, assignment: DeviceTrackingScanner) -> int:
    recent = list(
        reversed(
            db.execute(
                select(DeviceTrackingSample)
                .where(DeviceTrackingSample.assignment_id == assignment.id)
                .order_by(desc(DeviceTrackingSample.observed_at))
                .limit(TRACKING_STALE_HISTORY_LIMIT)
            ).scalars().all()
        )
    )
    capture_gaps: list[float] = []
    for previous, current in zip(recent, recent[1:]):
        if previous.boot_id != current.boot_id:
            continue
        gap = (ensure_utc(current.observed_at) - ensure_utc(previous.observed_at)).total_seconds()
        if 0 < gap <= TRACKING_STALE_MAX_SECONDS * 2:
            capture_gaps.append(gap)
    transport_delays = [
        max(
            0.0,
            (ensure_utc(sample.server_received_at) - ensure_utc(sample.observed_at)).total_seconds(),
        )
        for sample in recent
    ]
    cadence_p90 = _percentile(capture_gaps, 0.90) or 0.0
    transport_p90 = _percentile(transport_delays, 0.90) or 0.0
    adaptive = max(
        TRACKING_STALE_MIN_SECONDS,
        cadence_p90 * 2.0,
        transport_p90 + TRACKING_MEDIAN_WINDOW_SECONDS,
    )
    return int(min(TRACKING_STALE_MAX_SECONDS, ceil(adaptive)))


def tracking_window_median(
    db: Session,
    assignment: DeviceTrackingScanner,
    *,
    observed_at: datetime,
    boot_id: str,
    rssi: int,
) -> float:
    window_start = observed_at - timedelta(seconds=TRACKING_MEDIAN_WINDOW_SECONDS)
    values = list(
        db.execute(
            select(DeviceTrackingSample.rssi).where(
                DeviceTrackingSample.assignment_id == assignment.id,
                DeviceTrackingSample.boot_id == boot_id,
                DeviceTrackingSample.observed_at >= window_start,
                DeviceTrackingSample.observed_at <= observed_at,
                DeviceTrackingSample.delayed.is_(False),
            )
        ).scalars()
    )
    values.append(rssi)
    return float(median(values))


def _active_session(db: Session, session_id: str) -> DeviceTrackingSession:
    session = db.get(DeviceTrackingSession, session_id)
    if session is None:
        raise TrackingNotFoundError("tracking session not found")
    if session.state not in ACTIVE_SESSION_STATES:
        raise TrackingConflictError(f"tracking session is {session.state}")
    if ensure_utc(session.expires_at) <= utcnow():
        _finish_session(db, session, "expired", "lease_expired")
        db.commit()
        raise TrackingConflictError("tracking session lease expired")
    return session


def _assignments(db: Session, session_id: str) -> list[DeviceTrackingScanner]:
    return db.execute(
        select(DeviceTrackingScanner)
        .where(DeviceTrackingScanner.session_id == session_id)
        .order_by(DeviceTrackingScanner.scanner_id)
    ).scalars().all()


def _bump_scanner_config(db: Session, scanner_id: str) -> None:
    scanner = db.get(Scanner, scanner_id)
    if scanner is None:
        return
    scanner.config_version += 1
    config = db.execute(
        select(ScannerConfiguration).where(ScannerConfiguration.scanner_id == scanner_id)
    ).scalar_one_or_none()
    if config is None:
        config = ScannerConfiguration(scanner_id=scanner_id, version=scanner.config_version)
        db.add(config)
    else:
        config.version = scanner.config_version


def _tracking_targets(
    db: Session,
    logical_device_id: str,
    scanner_id: str,
) -> list[dict[str, str]]:
    identity_ids = db.execute(
        select(Observation.observed_identity_id)
        .where(
            Observation.logical_device_id == logical_device_id,
            Observation.scanner_id == scanner_id,
        )
        .order_by(desc(Observation.observed_at), desc(Observation.id))
        .limit(200)
    ).scalars()
    targets: list[dict[str, str]] = []
    seen: set[str] = set()
    for identity_id in identity_ids:
        if identity_id in seen:
            continue
        seen.add(identity_id)
        identity = db.get(ObservedIdentity, identity_id)
        if identity is None or not identity.address or not identity.address_type:
            continue
        targets.append(
            {
                "observed_identity_id": identity.id,
                "address": normalize_address(identity.address),
                "address_type": identity.address_type.strip().lower(),
            }
        )
        if len(targets) >= TRACKING_TARGET_LIMIT:
            break
    return targets


def serialize_tracking_session(
    db: Session,
    session: DeviceTrackingSession,
    *,
    include_history: bool = False,
) -> dict[str, Any]:
    assignments = _assignments(db, session.id)
    stale_by_assignment = {
        assignment.id: tracking_stale_seconds(db, assignment)
        for assignment in assignments
    }
    session_stale_seconds = max(
        stale_by_assignment.values(),
        default=TRACKING_STALE_MIN_SECONDS,
    )
    result: dict[str, Any] = {
        "id": session.id,
        "logical_device_id": session.logical_device_id,
        "mode": session.mode,
        "state": session.state,
        "started_at": serialize_datetime(session.started_at),
        "last_lease_at": serialize_datetime(session.last_lease_at),
        "expires_at": serialize_datetime(session.expires_at),
        "ended_at": serialize_datetime(session.ended_at),
        "stop_reason": session.stop_reason,
        "summary": session.summary or {},
        "assignments": [
            {
                "id": assignment.id,
                "scanner_id": assignment.scanner_id,
                "state": assignment.state,
                "target_identities": assignment.target_identities or [],
                "fixed_latitude": assignment.fixed_latitude,
                "fixed_longitude": assignment.fixed_longitude,
                "armed_at": serialize_datetime(assignment.armed_at),
                "last_sample_at": serialize_datetime(assignment.last_sample_at),
                "smoothed_rssi": assignment.smoothed_rssi,
                "dropped_samples": assignment.dropped_samples,
                "sample_stale_seconds": stale_by_assignment[assignment.id],
            }
            for assignment in assignments
        ],
        "lease_seconds": TRACKING_LEASE_SECONDS,
        "sample_stale_seconds": session_stale_seconds,
        "signal_scale": {
            "far_rssi": TRACKING_SIGNAL_FAR_RSSI,
            "near_rssi": TRACKING_SIGNAL_NEAR_RSSI,
            "filter": "time_window_median",
            "median_window_seconds": TRACKING_MEDIAN_WINDOW_SECONDS,
            "trend_current_seconds": TRACKING_TREND_CURRENT_SECONDS,
            "trend_previous_seconds": TRACKING_TREND_PREVIOUS_SECONDS,
            "stale_min_seconds": TRACKING_STALE_MIN_SECONDS,
            "stale_max_seconds": TRACKING_STALE_MAX_SECONDS,
        },
    }
    if include_history:
        samples = db.execute(
            select(DeviceTrackingSample)
            .where(DeviceTrackingSample.session_id == session.id)
            .order_by(desc(DeviceTrackingSample.observed_at))
            .limit(200)
        ).scalars().all()
        positions = db.execute(
            select(DeviceTrackingPosition)
            .where(DeviceTrackingPosition.session_id == session.id)
            .order_by(desc(DeviceTrackingPosition.observed_at))
            .limit(500)
        ).scalars().all()
        result["samples"] = [serialize_tracking_sample(sample) for sample in reversed(samples)]
        result["positions"] = [serialize_tracking_position(position) for position in reversed(positions)]
    return result


def serialize_tracking_sample(sample: DeviceTrackingSample) -> dict[str, Any]:
    return {
        "id": sample.id,
        "session_id": sample.session_id,
        "scanner_id": sample.scanner_id,
        "observed_identity_id": sample.observed_identity_id,
        "sample_id": sample.sample_id,
        "observed_at": serialize_datetime(sample.observed_at),
        "address": sample.address,
        "address_type": sample.address_type,
        "rssi": sample.rssi,
        "smoothed_rssi": sample.smoothed_rssi,
        "signal_level": sample.signal_level,
        "delayed": sample.delayed,
        "sequence": sample.sequence,
    }


def serialize_tracking_position(position: DeviceTrackingPosition) -> dict[str, Any]:
    return {
        "id": position.id,
        "session_id": position.session_id,
        "scanner_id": position.scanner_id,
        "position_id": position.position_id,
        "observed_at": serialize_datetime(position.observed_at),
        "latitude": position.latitude,
        "longitude": position.longitude,
        "accuracy_m": position.accuracy_m,
    }


def start_tracking_session(
    db: Session,
    logical_device_id: str,
    payload: TrackingSessionCreateIn,
) -> dict[str, Any]:
    device = db.get(LogicalDevice, logical_device_id)
    if device is None:
        raise TrackingNotFoundError("logical device not found")
    if device.ignored:
        raise TrackingValidationError("ignored devices cannot be tracked")

    latest_observation = db.execute(
        select(Observation)
        .where(Observation.logical_device_id == logical_device_id)
        .order_by(desc(Observation.observed_at), desc(Observation.id))
        .limit(1)
    ).scalar_one_or_none()
    if latest_observation is None:
        raise TrackingValidationError("a real BLE observation is required before tracking")

    scanner = db.get(Scanner, latest_observation.scanner_id)
    if scanner is None or not scanner.enabled:
        raise TrackingValidationError("the observing scanner is not available")

    now = utcnow()
    conflict = db.execute(
        select(DeviceTrackingScanner)
        .join(DeviceTrackingSession, DeviceTrackingSession.id == DeviceTrackingScanner.session_id)
        .where(
            DeviceTrackingScanner.scanner_id == scanner.id,
            DeviceTrackingSession.state.in_(ACTIVE_SESSION_STATES),
            DeviceTrackingSession.expires_at > now,
        )
        .limit(1)
    ).scalar_one_or_none()
    if conflict is not None:
        existing_session = db.get(DeviceTrackingSession, conflict.session_id)
        if existing_session is not None and existing_session.logical_device_id == logical_device_id:
            existing_session.last_lease_at = now
            existing_session.expires_at = now + timedelta(seconds=TRACKING_LEASE_SECONDS)
            db.commit()
            db.refresh(existing_session)
            return serialize_tracking_session(db, existing_session)
        raise TrackingConflictError("the scanner already has an active tracking session")

    targets = _tracking_targets(db, logical_device_id, scanner.id)
    if not targets:
        raise TrackingValidationError("the device has no trackable observed BLE identity")

    initial_state = "arming" if scanner.status == "online" else "scanner_offline"
    session = DeviceTrackingSession(
        logical_device_id=logical_device_id,
        mode=payload.mode,
        state=initial_state,
        started_at=now,
        last_lease_at=now,
        expires_at=now + timedelta(seconds=TRACKING_LEASE_SECONDS),
        summary={},
    )
    db.add(session)
    db.flush()
    assignment = DeviceTrackingScanner(
        session_id=session.id,
        scanner_id=scanner.id,
        state=initial_state,
        target_identities=targets,
        fixed_latitude=scanner.latitude,
        fixed_longitude=scanner.longitude,
    )
    db.add(assignment)
    _bump_scanner_config(db, scanner.id)
    create_event(
        db,
        "device_tracking_started",
        now,
        scanner_id=scanner.id,
        logical_device_id=device.id,
        confidence=1.0,
        reason="operator_started_signal_finder",
        details={"session_id": session.id, "mode": payload.mode},
        dedupe_key=f"tracking-started:{session.id}",
    )
    db.commit()
    db.refresh(session)
    return serialize_tracking_session(db, session)


def get_tracking_session(db: Session, session_id: str, *, include_history: bool = True) -> dict[str, Any]:
    session = db.get(DeviceTrackingSession, session_id)
    if session is None:
        raise TrackingNotFoundError("tracking session not found")
    return serialize_tracking_session(db, session, include_history=include_history)


def renew_tracking_lease(db: Session, session_id: str) -> dict[str, Any]:
    session = _active_session(db, session_id)
    now = utcnow()
    session.last_lease_at = now
    session.expires_at = now + timedelta(seconds=TRACKING_LEASE_SECONDS)
    db.commit()
    db.refresh(session)
    return serialize_tracking_session(db, session)


def _session_summary(db: Session, session: DeviceTrackingSession) -> dict[str, Any]:
    samples = db.execute(
        select(DeviceTrackingSample)
        .where(DeviceTrackingSample.session_id == session.id)
        .order_by(DeviceTrackingSample.observed_at)
    ).scalars().all()
    if not samples:
        return {"sample_count": 0}

    rssi_values = [sample.rssi for sample in samples]
    strongest = max(samples, key=lambda sample: (sample.rssi, ensure_utc(sample.observed_at)))
    positions = db.execute(
        select(DeviceTrackingPosition).where(DeviceTrackingPosition.session_id == session.id)
    ).scalars().all()
    nearest_position = None
    if positions:
        nearest_position = min(
            positions,
            key=lambda position: abs(
                (ensure_utc(position.observed_at) - ensure_utc(strongest.observed_at)).total_seconds()
            ),
        )
        if abs(
            (ensure_utc(nearest_position.observed_at) - ensure_utc(strongest.observed_at)).total_seconds()
        ) > 10:
            nearest_position = None
    return {
        "sample_count": len(samples),
        "minimum_rssi": min(rssi_values),
        "maximum_rssi": max(rssi_values),
        "median_rssi": float(median(rssi_values)),
        "strongest_measurement": {
            "scanner_id": strongest.scanner_id,
            "observed_at": serialize_datetime(strongest.observed_at),
            "rssi": strongest.rssi,
            "scanner_position": (
                {
                    "latitude": nearest_position.latitude,
                    "longitude": nearest_position.longitude,
                    "accuracy_m": nearest_position.accuracy_m,
                }
                if nearest_position is not None
                else None
            ),
        },
    }


def _finish_session(
    db: Session,
    session: DeviceTrackingSession,
    state: str,
    reason: str,
) -> dict[str, Any]:
    if session.state not in ACTIVE_SESSION_STATES:
        return serialize_tracking_session(db, session)
    now = utcnow()
    session.summary = _session_summary(db, session)
    session.state = state
    session.stop_reason = reason
    session.ended_at = now
    assignments = _assignments(db, session.id)
    for assignment in assignments:
        assignment.state = state
        _bump_scanner_config(db, assignment.scanner_id)
    create_event(
        db,
        "device_tracking_stopped",
        now,
        scanner_id=assignments[0].scanner_id if assignments else None,
        logical_device_id=session.logical_device_id,
        confidence=1.0,
        reason=reason,
        details={"session_id": session.id, "state": state, "summary": session.summary},
        dedupe_key=f"tracking-stopped:{session.id}",
    )
    return serialize_tracking_session(db, session)


def stop_tracking_session(db: Session, session_id: str, reason: str = "operator_stopped") -> dict[str, Any]:
    session = db.get(DeviceTrackingSession, session_id)
    if session is None:
        raise TrackingNotFoundError("tracking session not found")
    result = _finish_session(db, session, "stopped", reason)
    db.commit()
    return result


def tracking_focus_for_scanner(db: Session, scanner_id: str) -> dict[str, Any] | None:
    now = utcnow()
    assignment = db.execute(
        select(DeviceTrackingScanner)
        .join(DeviceTrackingSession, DeviceTrackingSession.id == DeviceTrackingScanner.session_id)
        .where(
            DeviceTrackingScanner.scanner_id == scanner_id,
            DeviceTrackingSession.state.in_(ACTIVE_SESSION_STATES),
            DeviceTrackingSession.expires_at > now,
        )
        .order_by(desc(DeviceTrackingSession.started_at))
        .limit(1)
    ).scalar_one_or_none()
    if assignment is None:
        return None
    session = db.get(DeviceTrackingSession, assignment.session_id)
    if session is None:
        return None
    return {
        "session_id": session.id,
        "mode": session.mode,
        "expires_at": serialize_datetime(session.expires_at),
        "sample_interval_ms": 200,
        "upload_interval_ms": 500,
        "target_identities": assignment.target_identities or [],
    }


def refresh_tracking_targets_for_scanner(
    db: Session,
    scanner_id: str,
) -> list[dict[str, Any]]:
    changed: list[dict[str, Any]] = []
    assignments = db.execute(
        select(DeviceTrackingScanner)
        .join(DeviceTrackingSession, DeviceTrackingSession.id == DeviceTrackingScanner.session_id)
        .where(
            DeviceTrackingScanner.scanner_id == scanner_id,
            DeviceTrackingSession.state.in_(ACTIVE_SESSION_STATES),
        )
    ).scalars().all()
    for assignment in assignments:
        session = db.get(DeviceTrackingSession, assignment.session_id)
        if session is None:
            continue
        targets = _tracking_targets(db, session.logical_device_id, scanner_id)
        if targets and targets != (assignment.target_identities or []):
            assignment.target_identities = targets
            assignment.state = "identity_changed"
            session.state = "identity_changed"
            _bump_scanner_config(db, scanner_id)
            changed.append(serialize_tracking_session(db, session))
    if changed:
        db.commit()
    return changed


def record_tracking_heartbeat(
    db: Session,
    scanner: Scanner,
    health: dict[str, Any],
) -> dict[str, Any] | None:
    session_id = str(health.get("tracking_session_id") or "").strip()
    tracking_state = str(health.get("tracking_state") or "").strip().lower()
    if not session_id or tracking_state not in {"active", "waiting", "inactive"}:
        return None
    assignment = db.execute(
        select(DeviceTrackingScanner).where(
            DeviceTrackingScanner.session_id == session_id,
            DeviceTrackingScanner.scanner_id == scanner.id,
        )
    ).scalar_one_or_none()
    session = db.get(DeviceTrackingSession, session_id)
    if assignment is None or session is None or session.state not in ACTIVE_SESSION_STATES:
        return None
    if tracking_state in {"active", "waiting"}:
        now = utcnow()
        assignment.armed_at = assignment.armed_at or now
        if assignment.state != "live":
            assignment.state = "waiting_for_advertisement"
        if session.state not in {"live", "stale"}:
            session.state = "waiting_for_advertisement"
    elif assignment.state != "scanner_offline":
        assignment.state = "arming"
        if session.state not in {"live", "stale"}:
            session.state = "arming"
    db.commit()
    return {
        "session_id": session.id,
        "state": session.state,
        "scanner_id": scanner.id,
    }


def _apply_tracking_presence_evidence(
    db: Session,
    *,
    session: DeviceTrackingSession,
    scanner: Scanner,
    identity: ObservedIdentity,
    observed_at: datetime,
    smoothed_rssi: float,
) -> None:
    """Update presence from an exact assigned BLE identity without moving its anchor."""
    device = db.get(LogicalDevice, session.logical_device_id)
    if device is None or device.ignored:
        return
    linked = db.execute(
        select(Observation.id).where(
            Observation.logical_device_id == device.id,
            Observation.observed_identity_id == identity.id,
        ).limit(1)
    ).scalar_one_or_none()
    if linked is None:
        return

    identity.last_seen_at = max(ensure_utc(identity.last_seen_at), observed_at)
    identity.observation_count += 1
    if observed_at < ensure_utc(device.last_seen_at):
        return

    previous_status = device.status
    device.last_seen_at = observed_at
    device.smoothed_rssi = smoothed_rssi
    device.observation_count += 1
    if previous_status in {"temporarily_missing", "offline", "identity_expired"}:
        device.status = "returned"
        create_event(
            db,
            "device_returned",
            observed_at,
            scanner_id=scanner.id,
            logical_device_id=device.id,
            observed_identity_id=identity.id,
            previous_state=previous_status,
            new_state="returned",
            confidence=1.0,
            reason="assigned_identity_observed_in_tracking_sample",
            details={
                "evidence": "direct_ble_tracking_sample",
                "location_anchor_changed": False,
            },
            dedupe_key=f"tracking-returned:{device.id}:{observed_at.strftime('%Y%m%d%H%M')}",
        )
    elif previous_status in {"newly_detected", "returned"}:
        device.status = "active"


def ingest_tracking_samples(
    db: Session,
    scanner: Scanner,
    payload: TrackingSampleBatchIn,
) -> dict[str, Any]:
    session = db.get(DeviceTrackingSession, payload.session_id)
    if session is None:
        raise TrackingNotFoundError("tracking session not found")
    assignment = db.execute(
        select(DeviceTrackingScanner).where(
            DeviceTrackingScanner.session_id == session.id,
            DeviceTrackingScanner.scanner_id == scanner.id,
        )
    ).scalar_one_or_none()
    if assignment is None:
        raise TrackingConflictError("scanner is not assigned to this tracking session")
    if session.state not in ACTIVE_SESSION_STATES:
        return {
            "accepted": 0,
            "duplicates": 0,
            "rejected": 0,
            "discarded": len(payload.samples),
            "session_id": session.id,
            "state": session.state,
            "live_samples": [],
        }
    if ensure_utc(session.expires_at) <= utcnow():
        _finish_session(db, session, "expired", "lease_expired")
        db.commit()
        return {
            "accepted": 0,
            "duplicates": 0,
            "rejected": 0,
            "discarded": len(payload.samples),
            "session_id": session.id,
            "state": session.state,
            "live_samples": [],
        }

    targets = {
        (
            normalize_address(str(target.get("address") or "")),
            str(target.get("address_type") or "").strip().lower(),
        ): str(target.get("observed_identity_id") or "")
        for target in assignment.target_identities or []
    }
    existing_ids = set(
        db.execute(
            select(DeviceTrackingSample.sample_id).where(
                DeviceTrackingSample.scanner_id == scanner.id,
                DeviceTrackingSample.sample_id.in_([sample.sample_id for sample in payload.samples]),
            )
        ).scalars()
    )
    accepted = 0
    duplicates = 0
    rejected = 0
    live_samples: list[dict[str, Any]] = []
    received_at = utcnow()
    stale_seconds = tracking_stale_seconds(db, assignment)

    for item in payload.samples:
        if item.sample_id in existing_ids:
            duplicates += 1
            continue
        address = normalize_address(item.address)
        address_type = item.address_type.strip().lower()
        identity_id = targets.get((address, address_type))
        identity = db.get(ObservedIdentity, identity_id) if identity_id else None
        if identity is None:
            rejected += 1
            continue

        observed_at = ensure_utc(item.observed_at)
        if observed_at > received_at + timedelta(minutes=5):
            observed_at = received_at
        age_seconds = (received_at - observed_at).total_seconds()
        sequence_is_current = (
            assignment.last_boot_id != item.boot_id
            or assignment.last_sequence is None
            or item.sequence > assignment.last_sequence
        )
        delayed = age_seconds > stale_seconds or not sequence_is_current
        if assignment.last_boot_id != item.boot_id or assignment.smoothed_rssi is None:
            smoothed = float(item.rssi)
        elif sequence_is_current:
            smoothed = tracking_window_median(
                db,
                assignment,
                observed_at=observed_at,
                boot_id=item.boot_id,
                rssi=item.rssi,
            )
        else:
            smoothed = assignment.smoothed_rssi

        sample = DeviceTrackingSample(
            session_id=session.id,
            assignment_id=assignment.id,
            scanner_id=scanner.id,
            observed_identity_id=identity_id,
            batch_id=payload.batch_id,
            sample_id=item.sample_id,
            observed_at=observed_at,
            server_received_at=received_at,
            boot_id=item.boot_id,
            monotonic_ms=item.monotonic_ms,
            sequence=item.sequence,
            address=address,
            address_type=address_type,
            rssi=item.rssi,
            smoothed_rssi=smoothed,
            signal_level=signal_level(smoothed),
            delayed=delayed,
        )
        db.add(sample)
        db.flush()
        existing_ids.add(item.sample_id)
        accepted += 1

        if sequence_is_current:
            assignment.last_boot_id = item.boot_id
            assignment.last_sequence = item.sequence
            if not delayed:
                assignment.last_sample_at = max(
                    ensure_utc(assignment.last_sample_at) if assignment.last_sample_at else observed_at,
                    observed_at,
                )
                assignment.smoothed_rssi = smoothed
                _apply_tracking_presence_evidence(
                    db,
                    session=session,
                    scanner=scanner,
                    identity=identity,
                    observed_at=observed_at,
                    smoothed_rssi=smoothed,
                )
        if not delayed:
            assignment.state = "live"
            session.state = "live"
            live_samples.append(serialize_tracking_sample(sample))

    assignment.dropped_samples += payload.dropped_samples
    db.commit()
    return {
        "accepted": accepted,
        "duplicates": duplicates,
        "rejected": rejected,
        "discarded": 0,
        "session_id": session.id,
        "state": session.state,
        "live_samples": live_samples,
    }


def record_tracking_position(
    db: Session,
    session_id: str,
    payload: TrackingPositionIn,
) -> dict[str, Any]:
    session = _active_session(db, session_id)
    if session.mode != "walk":
        raise TrackingConflictError("scanner positions are accepted only in walk mode")
    assignment = db.execute(
        select(DeviceTrackingScanner).where(
            DeviceTrackingScanner.session_id == session.id,
            DeviceTrackingScanner.scanner_id == payload.scanner_id,
        )
    ).scalar_one_or_none()
    if assignment is None:
        raise TrackingConflictError("scanner is not assigned to this tracking session")
    existing = db.execute(
        select(DeviceTrackingPosition).where(
            DeviceTrackingPosition.session_id == session.id,
            DeviceTrackingPosition.position_id == payload.position_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return serialize_tracking_position(existing)

    received_at = utcnow()
    observed_at = ensure_utc(payload.observed_at)
    if observed_at > received_at + timedelta(minutes=5):
        observed_at = received_at
    position = DeviceTrackingPosition(
        session_id=session.id,
        scanner_id=payload.scanner_id,
        position_id=payload.position_id,
        observed_at=observed_at,
        server_received_at=received_at,
        latitude=payload.latitude,
        longitude=payload.longitude,
        accuracy_m=payload.accuracy_m,
    )
    db.add(position)
    db.commit()
    db.refresh(position)
    return serialize_tracking_position(position)


def refresh_tracking_states(db: Session) -> list[dict[str, Any]]:
    now = utcnow()
    changed: list[dict[str, Any]] = []
    sessions = db.execute(
        select(DeviceTrackingSession).where(DeviceTrackingSession.state.in_(ACTIVE_SESSION_STATES))
    ).scalars().all()
    for session in sessions:
        if ensure_utc(session.expires_at) <= now:
            changed.append(_finish_session(db, session, "expired", "lease_expired"))
            continue
        assignments = _assignments(db, session.id)
        for assignment in assignments:
            scanner = db.get(Scanner, assignment.scanner_id)
            if scanner is None or scanner.status != "online":
                if assignment.state != "scanner_offline":
                    assignment.state = "scanner_offline"
                    session.state = "scanner_offline"
                    changed.append(serialize_tracking_session(db, session))
                continue
            if (
                assignment.last_sample_at is not None
                and (now - ensure_utc(assignment.last_sample_at)).total_seconds()
                > tracking_stale_seconds(db, assignment)
                and assignment.state == "live"
            ):
                assignment.state = "stale"
                session.state = "stale"
                changed.append(serialize_tracking_session(db, session))
    if changed:
        db.commit()
    return changed


def cleanup_tracking_history(db: Session, *, batch_size: int = 1000) -> dict[str, int]:
    now = utcnow()
    raw_setting = db.get(SystemSetting, "raw_observation_retention_days")
    summary_setting = db.get(SystemSetting, "summary_retention_days")
    raw_days = max(1, int(raw_setting.value if raw_setting is not None else 30))
    summary_days = max(raw_days, int(summary_setting.value if summary_setting is not None else 365))

    sample_ids = select(DeviceTrackingSample.id).where(
        DeviceTrackingSample.observed_at < now - timedelta(days=raw_days)
    ).limit(batch_size)
    position_ids = select(DeviceTrackingPosition.id).where(
        DeviceTrackingPosition.observed_at < now - timedelta(days=raw_days)
    ).limit(batch_size)
    deleted_samples = db.execute(
        delete(DeviceTrackingSample).where(DeviceTrackingSample.id.in_(sample_ids))
    ).rowcount or 0
    deleted_positions = db.execute(
        delete(DeviceTrackingPosition).where(DeviceTrackingPosition.id.in_(position_ids))
    ).rowcount or 0

    old_session_ids = list(
        db.execute(
            select(DeviceTrackingSession.id)
            .where(
                DeviceTrackingSession.ended_at.is_not(None),
                DeviceTrackingSession.ended_at < now - timedelta(days=summary_days),
            )
            .limit(batch_size)
        ).scalars()
    )
    deleted_sessions = 0
    if old_session_ids:
        db.execute(
            delete(DeviceTrackingSample).where(DeviceTrackingSample.session_id.in_(old_session_ids))
        )
        db.execute(
            delete(DeviceTrackingPosition).where(DeviceTrackingPosition.session_id.in_(old_session_ids))
        )
        db.execute(
            delete(DeviceTrackingScanner).where(DeviceTrackingScanner.session_id.in_(old_session_ids))
        )
        deleted_sessions = db.execute(
            delete(DeviceTrackingSession).where(DeviceTrackingSession.id.in_(old_session_ids))
        ).rowcount or 0
    db.commit()
    return {
        "tracking_samples": deleted_samples,
        "tracking_positions": deleted_positions,
        "tracking_sessions": deleted_sessions,
    }
