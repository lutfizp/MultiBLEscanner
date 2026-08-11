from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    DeviceEnrichment,
    DeviceEvent,
    DeviceLocationEstimate,
    DeviceTrackingSession,
    LogicalDevice,
    Observation,
    ObservedIdentity,
)
from .processing import ensure_utc, utcnow


@dataclass(frozen=True)
class MergeResult:
    canonical_device: LogicalDevice
    merged_device: LogicalDevice
    previous_scanner_id: str | None
    previous_zone: str | None
    merged_observation_count: int


@dataclass(frozen=True)
class SplitResult:
    source_device: LogicalDevice
    split_device: LogicalDevice
    moved_observation_count: int


def _combined_notes(first: str | None, second: str | None) -> str | None:
    values = [value.strip() for value in (first, second) if value and value.strip()]
    return "\n\n".join(dict.fromkeys(values)) or None


def _combined_tags(first: Any, second: Any) -> list[str]:
    tags: list[str] = []
    for value in [*(first or []), *(second or [])]:
        normalized = str(value).strip()
        if normalized and normalized not in tags:
            tags.append(normalized)
    return tags


def _looks_like_address(value: str | None) -> bool:
    return bool(value and value.count(":") == 5 and len(value) == 17)


def _parsed_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return ensure_utc(value)
    if isinstance(value, str):
        try:
            return ensure_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            pass
    return ensure_utc(fallback)


def merge_logical_devices(
    db: Session,
    *,
    canonical: LogicalDevice,
    merged: LogicalDevice,
) -> MergeResult:
    """Merge every mutable device-owned record through one canonical path."""
    if canonical.id == merged.id:
        raise ValueError("source and target logical devices must differ")
    if canonical.status == "merged":
        raise ValueError("target logical device is already merged")

    previous_scanner_id = canonical.current_scanner_id
    previous_zone = canonical.current_zone
    merged_observation_count = merged.observation_count or 0
    merged_is_newer = ensure_utc(merged.last_seen_at) >= ensure_utc(canonical.last_seen_at)

    if merged_is_newer:
        for field in (
            "status",
            "movement_status",
            "location_confidence",
            "movement_confidence",
            "current_scanner_id",
            "current_zone",
            "proximity_band",
            "estimated_distance_m",
            "smoothed_rssi",
            "latitude",
            "longitude",
            "location_anchor_observed_at",
            "last_seen_at",
            "primary_address",
            "primary_address_type",
        ):
            setattr(canonical, field, getattr(merged, field))

    if (
        not canonical.display_name
        or _looks_like_address(canonical.display_name)
    ) and merged.display_name:
        canonical.display_name = merged.display_name
    canonical.alias = canonical.alias or merged.alias
    canonical.vendor = canonical.vendor or merged.vendor
    canonical.category = canonical.category or merged.category
    canonical.known = bool(canonical.known or merged.known)
    canonical.identity_confidence = max(
        canonical.identity_confidence or 0.0,
        merged.identity_confidence or 0.0,
    )
    canonical.first_seen_at = min(
        ensure_utc(canonical.first_seen_at),
        ensure_utc(merged.first_seen_at),
    )
    canonical.observation_count = (canonical.observation_count or 0) + merged_observation_count
    canonical.notes = _combined_notes(canonical.notes, merged.notes)
    canonical.tags = _combined_tags(canonical.tags, merged.tags)
    canonical.ignored = False

    for model in (
        Observation,
        DeviceLocationEstimate,
        DeviceEvent,
        DeviceEnrichment,
        DeviceTrackingSession,
    ):
        db.execute(
            model.__table__.update()
            .where(model.logical_device_id == merged.id)
            .values(logical_device_id=canonical.id)
        )

    merged.ignored = True
    merged.status = "merged"
    merged.movement_status = "merged"
    merged.current_scanner_id = None
    merged.current_zone = None
    merged.latitude = None
    merged.longitude = None
    merged.location_anchor_observed_at = None
    merged.estimated_distance_m = None
    merged.smoothed_rssi = None
    merged.location_confidence = 0.0
    merged.movement_confidence = 0.0

    return MergeResult(
        canonical_device=canonical,
        merged_device=merged,
        previous_scanner_id=previous_scanner_id,
        previous_zone=previous_zone,
        merged_observation_count=merged_observation_count,
    )


def _status_for_last_seen(
    last_seen_at: datetime,
    randomized: bool,
    settings: Settings,
) -> str:
    age = max(0.0, (utcnow() - ensure_utc(last_seen_at)).total_seconds())
    if randomized and age >= settings.presence_missing_seconds:
        return "identity_expired"
    if age >= settings.presence_offline_seconds:
        return "offline"
    if age >= settings.presence_missing_seconds:
        return "temporarily_missing"
    return "active"


def _refresh_device_from_observations(
    db: Session,
    device: LogicalDevice,
    settings: Settings,
) -> None:
    aggregate = db.execute(
        select(
            func.count(Observation.id),
            func.min(Observation.observed_at),
            func.max(Observation.observed_at),
        ).where(Observation.logical_device_id == device.id)
    ).one()
    observation_count, first_seen_at, last_seen_at = aggregate
    if not observation_count:
        device.status = "split_empty"
        device.movement_status = "stationary"
        device.ignored = True
        device.observation_count = 0
        device.current_scanner_id = None
        device.current_zone = None
        device.latitude = None
        device.longitude = None
        device.location_anchor_observed_at = None
        device.estimated_distance_m = None
        device.smoothed_rssi = None
        return

    latest = db.execute(
        select(Observation)
        .where(Observation.logical_device_id == device.id)
        .order_by(desc(Observation.observed_at), desc(Observation.id))
        .limit(1)
    ).scalar_one()
    identity = db.get(ObservedIdentity, latest.observed_identity_id)
    notes = latest.processing_notes if isinstance(latest.processing_notes, dict) else {}
    proximity = notes.get("proximity_model") if isinstance(notes.get("proximity_model"), dict) else {}
    movement = notes.get("movement_evidence") if isinstance(notes.get("movement_evidence"), dict) else {}
    snapshot = notes.get("anchor_snapshot") if isinstance(notes.get("anchor_snapshot"), dict) else {}
    latest_estimate = db.execute(
        select(DeviceLocationEstimate)
        .where(
            DeviceLocationEstimate.logical_device_id == device.id,
            DeviceLocationEstimate.estimated_at <= latest.observed_at,
        )
        .order_by(desc(DeviceLocationEstimate.estimated_at), desc(DeviceLocationEstimate.id))
        .limit(1)
    ).scalar_one_or_none()
    estimate_details = (
        latest_estimate.details
        if latest_estimate is not None and isinstance(latest_estimate.details, dict)
        else {}
    )

    device.first_seen_at = ensure_utc(first_seen_at)
    device.last_seen_at = ensure_utc(last_seen_at)
    device.observation_count = int(observation_count)
    device.ignored = False
    device.primary_address = identity.address if identity else device.primary_address
    device.primary_address_type = identity.address_type if identity else device.primary_address_type
    if identity and not device.alias:
        device.display_name = identity.local_name or identity.advertised_name or identity.address
    randomized = bool(identity.randomized_address) if identity else False
    device.status = _status_for_last_seen(device.last_seen_at, randomized, settings)
    device.movement_status = movement.get("applied_status") or "stationary"
    device.movement_confidence = 0.0
    device.current_scanner_id = latest.scanner_id
    device.current_zone = snapshot.get("zone") or (latest_estimate.zone if latest_estimate else None)
    device.proximity_band = proximity.get("band") or "unknown"
    device.estimated_distance_m = latest.estimated_distance_m
    device.smoothed_rssi = proximity.get("smoothed_rssi", float(latest.rssi))
    device.location_confidence = latest_estimate.confidence if latest_estimate else 0.0
    device.latitude = snapshot.get("latitude", estimate_details.get("anchor_latitude"))
    device.longitude = snapshot.get("longitude", estimate_details.get("anchor_longitude"))
    device.location_anchor_observed_at = _parsed_datetime(
        snapshot.get("anchored_at"),
        latest_estimate.estimated_at if latest_estimate else latest.observed_at,
    )


def split_logical_identity(
    db: Session,
    *,
    source: LogicalDevice,
    observed_identity: ObservedIdentity,
    settings: Settings,
    target: LogicalDevice | None = None,
) -> SplitResult:
    """Move one observed identity and its attributable evidence out of a device."""
    observations = db.execute(
        select(Observation)
        .where(
            Observation.logical_device_id == source.id,
            Observation.observed_identity_id == observed_identity.id,
        )
        .order_by(Observation.observed_at)
    ).scalars().all()
    if not observations:
        raise ValueError("observed identity does not belong to the source logical device")
    if target is not None and target.id == source.id:
        raise ValueError("split target must differ from the source logical device")

    first_observation = observations[0]
    last_observation = observations[-1]
    split_device = target
    if split_device is None:
        split_device = LogicalDevice(
            primary_address=observed_identity.address,
            primary_address_type=observed_identity.address_type or "unknown",
            display_name=(
                observed_identity.local_name
                or observed_identity.advertised_name
                or observed_identity.address
                or "Unknown BLE device"
            ),
            status="active",
            movement_status="stationary",
            identity_confidence=0.55 if observed_identity.randomized_address else 0.85,
            first_seen_at=first_observation.observed_at,
            last_seen_at=last_observation.observed_at,
            observation_count=0,
            category=source.category,
            vendor=source.vendor,
        )
        db.add(split_device)
        db.flush()

    estimate_ids = list(
        db.execute(
            select(DeviceLocationEstimate.id)
            .join(
                Observation,
                and_(
                    Observation.logical_device_id == DeviceLocationEstimate.logical_device_id,
                    Observation.scanner_id == DeviceLocationEstimate.scanner_id,
                    Observation.observed_at == DeviceLocationEstimate.estimated_at,
                ),
            )
            .where(
                DeviceLocationEstimate.logical_device_id == source.id,
                Observation.observed_identity_id == observed_identity.id,
            )
            .distinct()
        ).scalars()
    )

    db.execute(
        Observation.__table__.update()
        .where(
            Observation.logical_device_id == source.id,
            Observation.observed_identity_id == observed_identity.id,
        )
        .values(logical_device_id=split_device.id)
    )
    if estimate_ids:
        db.execute(
            DeviceLocationEstimate.__table__.update()
            .where(DeviceLocationEstimate.id.in_(estimate_ids))
            .values(logical_device_id=split_device.id)
        )
    for model in (DeviceEnrichment, DeviceEvent):
        db.execute(
            model.__table__.update()
            .where(
                model.logical_device_id == source.id,
                model.observed_identity_id == observed_identity.id,
            )
            .values(logical_device_id=split_device.id)
        )

    db.flush()
    _refresh_device_from_observations(db, split_device, settings)
    _refresh_device_from_observations(db, source, settings)
    return SplitResult(
        source_device=source,
        split_device=split_device,
        moved_observation_count=len(observations),
    )
