from __future__ import annotations

import json
import os

from sqlalchemy import delete, or_, select

from .config import get_settings
from .database import Session, SessionLocal
from .models import (
    DeviceEnrichment,
    DeviceEvent,
    DeviceIdentityCorrelation,
    DeviceLocationEstimate,
    DeviceTrackingPosition,
    DeviceTrackingSample,
    DeviceTrackingScanner,
    DeviceTrackingSession,
    LogicalDevice,
    ManualDeviceCorrelationDecision,
    Observation,
    ObservedIdentity,
    ProcessingError,
    Scanner,
    ScannerConfiguration,
    ScannerHeartbeat,
)
from .processing import is_synthetic_address_pattern
from .security import generate_scanner_token, hash_scanner_token
from .services import ensure_default_settings


def ensure_local_scanner() -> dict[str, object]:
    settings = get_settings()
    scanner_id = os.getenv("LOCAL_SCANNER_ID", "scn_dev_lab_001")
    scanner_name = os.getenv("LOCAL_SCANNER_NAME", "USB ESP32 Scanner")
    hardware_id = os.getenv("LOCAL_SCANNER_HARDWARE_ID", "usb-esp32-001")
    installation_name = os.getenv("LOCAL_SCANNER_INSTALLATION_NAME", "local-usb")
    building = os.getenv("LOCAL_SCANNER_BUILDING", "Local")
    floor = os.getenv("LOCAL_SCANNER_FLOOR", "1")
    room = os.getenv("LOCAL_SCANNER_ROOM", "ESP32")
    zone = os.getenv("LOCAL_SCANNER_ZONE", "USB Scanner")
    token = os.getenv("LOCAL_SCANNER_TOKEN") or generate_scanner_token()
    with SessionLocal() as db:
        ensure_default_settings(db)

        scanner = db.get(Scanner, scanner_id)
        if scanner is None:
            scanner = Scanner(
                id=scanner_id,
                display_name=scanner_name,
                hardware_id=hardware_id,
                token_hash=hash_scanner_token(token, settings.scanner_token_salt),
                installation_name=installation_name,
                building=building,
                floor=floor,
                room=room,
                zone=zone,
                latitude=None,
                longitude=None,
                status="registered",
                firmware_version="usb-serial",
                hardware_version="esp32-d0wd-v3",
            )
            db.add(scanner)
            db.flush()
            db.add(ScannerConfiguration(scanner_id=scanner.id))
        else:
            scanner.display_name = scanner_name
            scanner.hardware_id = hardware_id
            scanner.installation_name = installation_name
            if scanner.building in {None, "Development"}:
                scanner.building = building
            if scanner.floor in {None, "1"}:
                scanner.floor = floor
            if scanner.room in {None, "Lab"}:
                scanner.room = room
            if scanner.zone in {None, "Bench"}:
                scanner.zone = zone
            scanner.token_hash = hash_scanner_token(token, settings.scanner_token_salt)

        db.commit()
        return {
            "scanner_id": scanner.id,
            "scanner_token": token,
            "location": zone,
            "note": "Local USB scanner token. Store it securely.",
        }


def clear_scan_data_in_session(db: Session) -> dict[str, int]:
    """Remove scanner runtime data without deleting scanner setup or settings."""
    deleted = {
        "tracking_positions": db.execute(delete(DeviceTrackingPosition)).rowcount or 0,
        "tracking_samples": db.execute(delete(DeviceTrackingSample)).rowcount or 0,
        "tracking_scanners": db.execute(delete(DeviceTrackingScanner)).rowcount or 0,
        "tracking_sessions": db.execute(delete(DeviceTrackingSession)).rowcount or 0,
        "identity_correlations": db.execute(delete(DeviceIdentityCorrelation)).rowcount or 0,
        "device_enrichments": db.execute(delete(DeviceEnrichment)).rowcount or 0,
        "events": db.execute(delete(DeviceEvent)).rowcount or 0,
        "location_estimates": db.execute(delete(DeviceLocationEstimate)).rowcount or 0,
        "observations": db.execute(delete(Observation)).rowcount or 0,
        "processing_errors": db.execute(delete(ProcessingError)).rowcount or 0,
        "manual_decisions": db.execute(delete(ManualDeviceCorrelationDecision)).rowcount or 0,
        "heartbeats": db.execute(delete(ScannerHeartbeat)).rowcount or 0,
        "logical_devices": db.execute(delete(LogicalDevice)).rowcount or 0,
        "observed_identities": db.execute(delete(ObservedIdentity)).rowcount or 0,
        "scanner_runtime_resets": 0,
    }
    for scanner in db.execute(select(Scanner)).scalars():
        scanner.last_connection_at = None
        scanner.last_heartbeat_at = None
        scanner.last_seen_at = None
        scanner.status = "registered" if scanner.enabled else "disabled"
        scanner.uptime_seconds = None
        scanner.reset_reason = None
        scanner.network_info = {}
        deleted["scanner_runtime_resets"] += 1
    return deleted


def clear_scan_data() -> dict[str, int]:
    with SessionLocal() as db:
        deleted = clear_scan_data_in_session(db)
        db.commit()
        return deleted


def purge_suspicious_scan_data() -> dict[str, int]:
    with SessionLocal() as db:
        logical_ids = {
            logical_id
            for logical_id, primary_address in db.execute(select(LogicalDevice.id, LogicalDevice.primary_address))
            if is_synthetic_address_pattern(primary_address)
        }
        identity_ids = {
            identity_id
            for identity_id, address in db.execute(select(ObservedIdentity.id, ObservedIdentity.address))
            if is_synthetic_address_pattern(address)
        }

        if logical_ids or identity_ids:
            filters = []
            if logical_ids:
                filters.append(Observation.logical_device_id.in_(logical_ids))
            if identity_ids:
                filters.append(Observation.observed_identity_id.in_(identity_ids))
            for logical_id, identity_id in db.execute(
                select(Observation.logical_device_id, Observation.observed_identity_id).where(or_(*filters))
            ):
                logical_ids.add(logical_id)
                identity_ids.add(identity_id)

        deleted = {
            "tracking_positions": 0,
            "tracking_samples": 0,
            "tracking_scanners": 0,
            "tracking_sessions": 0,
            "device_enrichments": 0,
            "events": 0,
            "location_estimates": 0,
            "observations": 0,
            "processing_errors": 0,
            "manual_decisions": 0,
            "identity_correlations": 0,
            "logical_devices": 0,
            "observed_identities": 0,
        }

        if logical_ids:
            tracking_session_ids = list(
                db.execute(
                    select(DeviceTrackingSession.id).where(
                        DeviceTrackingSession.logical_device_id.in_(logical_ids)
                    )
                ).scalars()
            )
            if tracking_session_ids:
                deleted["tracking_positions"] += db.execute(
                    delete(DeviceTrackingPosition).where(
                        DeviceTrackingPosition.session_id.in_(tracking_session_ids)
                    )
                ).rowcount or 0
                deleted["tracking_samples"] += db.execute(
                    delete(DeviceTrackingSample).where(
                        DeviceTrackingSample.session_id.in_(tracking_session_ids)
                    )
                ).rowcount or 0
                deleted["tracking_scanners"] += db.execute(
                    delete(DeviceTrackingScanner).where(
                        DeviceTrackingScanner.session_id.in_(tracking_session_ids)
                    )
                ).rowcount or 0
                deleted["tracking_sessions"] += db.execute(
                    delete(DeviceTrackingSession).where(
                        DeviceTrackingSession.id.in_(tracking_session_ids)
                    )
                ).rowcount or 0
            deleted["device_enrichments"] += db.execute(
                delete(DeviceEnrichment).where(DeviceEnrichment.logical_device_id.in_(logical_ids))
            ).rowcount or 0
            deleted["identity_correlations"] += db.execute(
                delete(DeviceIdentityCorrelation).where(
                    DeviceIdentityCorrelation.predecessor_logical_device_id.in_(logical_ids)
                )
            ).rowcount or 0
            deleted["identity_correlations"] += db.execute(
                delete(DeviceIdentityCorrelation).where(
                    DeviceIdentityCorrelation.successor_logical_device_id.in_(logical_ids)
                )
            ).rowcount or 0
            deleted["manual_decisions"] += db.execute(
                delete(ManualDeviceCorrelationDecision).where(
                    ManualDeviceCorrelationDecision.source_logical_device_id.in_(logical_ids)
                )
            ).rowcount or 0
            deleted["manual_decisions"] += db.execute(
                delete(ManualDeviceCorrelationDecision).where(
                    ManualDeviceCorrelationDecision.target_logical_device_id.in_(logical_ids)
                )
            ).rowcount or 0
            deleted["events"] += db.execute(
                delete(DeviceEvent).where(DeviceEvent.logical_device_id.in_(logical_ids))
            ).rowcount or 0
            deleted["location_estimates"] += db.execute(
                delete(DeviceLocationEstimate).where(DeviceLocationEstimate.logical_device_id.in_(logical_ids))
            ).rowcount or 0

        if identity_ids:
            deleted["tracking_samples"] += db.execute(
                delete(DeviceTrackingSample).where(
                    DeviceTrackingSample.observed_identity_id.in_(identity_ids)
                )
            ).rowcount or 0
            deleted["device_enrichments"] += db.execute(
                delete(DeviceEnrichment).where(DeviceEnrichment.observed_identity_id.in_(identity_ids))
            ).rowcount or 0
            deleted["identity_correlations"] += db.execute(
                delete(DeviceIdentityCorrelation).where(
                    DeviceIdentityCorrelation.predecessor_identity_id.in_(identity_ids)
                )
            ).rowcount or 0
            deleted["identity_correlations"] += db.execute(
                delete(DeviceIdentityCorrelation).where(
                    DeviceIdentityCorrelation.successor_identity_id.in_(identity_ids)
                )
            ).rowcount or 0
            deleted["manual_decisions"] += db.execute(
                delete(ManualDeviceCorrelationDecision).where(
                    ManualDeviceCorrelationDecision.observed_identity_id.in_(identity_ids)
                )
            ).rowcount or 0
            deleted["events"] += db.execute(
                delete(DeviceEvent).where(DeviceEvent.observed_identity_id.in_(identity_ids))
            ).rowcount or 0

        if logical_ids or identity_ids:
            filters = []
            if logical_ids:
                filters.append(Observation.logical_device_id.in_(logical_ids))
            if identity_ids:
                filters.append(Observation.observed_identity_id.in_(identity_ids))
            deleted["observations"] = db.execute(delete(Observation).where(or_(*filters))).rowcount or 0

        if logical_ids:
            deleted["logical_devices"] = db.execute(delete(LogicalDevice).where(LogicalDevice.id.in_(logical_ids))).rowcount or 0
        if identity_ids:
            deleted["observed_identities"] = db.execute(
                delete(ObservedIdentity).where(ObservedIdentity.id.in_(identity_ids))
            ).rowcount or 0

        db.commit()
        return deleted


if __name__ == "__main__":
    print(json.dumps(ensure_local_scanner(), indent=2))
