from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    DeviceEnrichment,
    DeviceEvent,
    DeviceLocationEstimate,
    DeviceTrackingPosition,
    DeviceTrackingSample,
    DeviceTrackingScanner,
    DeviceTrackingSession,
    Observation,
    ProcessingError,
    ScannerHeartbeat,
)
from .processing import utcnow


def _delete_batch(
    db: Session,
    model: Any,
    predicate: Any,
    *,
    batch_size: int,
) -> int:
    ids = list(
        db.execute(
            select(model.id)
            .where(predicate)
            .order_by(model.id)
            .limit(batch_size)
        ).scalars()
    )
    if not ids:
        return 0
    return db.execute(delete(model).where(model.id.in_(ids))).rowcount or 0


def cleanup_retained_history(
    db: Session,
    settings: Settings,
    *,
    batch_size: int = 1000,
) -> dict[str, int]:
    """Apply bounded raw and summary retention without deleting current state."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")

    now = utcnow()
    raw_cutoff = now - timedelta(days=settings.raw_observation_retention_days)
    summary_cutoff = now - timedelta(days=settings.summary_retention_days)

    deleted = {
        "observations": _delete_batch(
            db,
            Observation,
            Observation.observed_at < raw_cutoff,
            batch_size=batch_size,
        ),
        "location_estimates": _delete_batch(
            db,
            DeviceLocationEstimate,
            DeviceLocationEstimate.estimated_at < raw_cutoff,
            batch_size=batch_size,
        ),
        "heartbeats": _delete_batch(
            db,
            ScannerHeartbeat,
            ScannerHeartbeat.received_at < raw_cutoff,
            batch_size=batch_size,
        ),
        "tracking_samples": _delete_batch(
            db,
            DeviceTrackingSample,
            DeviceTrackingSample.observed_at < raw_cutoff,
            batch_size=batch_size,
        ),
        "tracking_positions": _delete_batch(
            db,
            DeviceTrackingPosition,
            DeviceTrackingPosition.observed_at < raw_cutoff,
            batch_size=batch_size,
        ),
        "device_enrichments": _delete_batch(
            db,
            DeviceEnrichment,
            DeviceEnrichment.enriched_at < summary_cutoff,
            batch_size=batch_size,
        ),
        "events": _delete_batch(
            db,
            DeviceEvent,
            DeviceEvent.occurred_at < summary_cutoff,
            batch_size=batch_size,
        ),
        "processing_errors": _delete_batch(
            db,
            ProcessingError,
            ProcessingError.created_at < summary_cutoff,
            batch_size=batch_size,
        ),
        "tracking_sessions": 0,
    }

    old_session_ids = list(
        db.execute(
            select(DeviceTrackingSession.id)
            .where(
                DeviceTrackingSession.ended_at.is_not(None),
                DeviceTrackingSession.ended_at < summary_cutoff,
            )
            .order_by(DeviceTrackingSession.id)
            .limit(batch_size)
        ).scalars()
    )
    if old_session_ids:
        db.execute(
            delete(DeviceTrackingSample).where(
                DeviceTrackingSample.session_id.in_(old_session_ids)
            )
        )
        db.execute(
            delete(DeviceTrackingPosition).where(
                DeviceTrackingPosition.session_id.in_(old_session_ids)
            )
        )
        db.execute(
            delete(DeviceTrackingScanner).where(
                DeviceTrackingScanner.session_id.in_(old_session_ids)
            )
        )
        deleted["tracking_sessions"] = db.execute(
            delete(DeviceTrackingSession).where(
                DeviceTrackingSession.id.in_(old_session_ids)
            )
        ).rowcount or 0

    db.commit()
    return deleted
