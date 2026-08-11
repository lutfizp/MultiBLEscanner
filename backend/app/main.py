from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import logging
from threading import Lock
import time
from typing import Annotated
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError, TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import SessionLocal, get_db
from .models import Scanner
from .realtime import broker, tracking_broker
from .retention import cleanup_retained_history
from .schemas import (
    BrowserLocationDiagnosticIn,
    DevicePatchIn,
    GATTEnrichmentReportIn,
    HeartbeatIn,
    ManualCorrelationIn,
    ObservationBatchIn,
    ScannerPatchIn,
    ScannerPositionIn,
    ScannerRegistrationIn,
    ScannerRegistrationOut,
    SettingsPatchIn,
    TrackingPositionIn,
    TrackingSampleBatchIn,
    TrackingSessionCreateIn,
)
from .services import (
    apply_manual_correlation,
    authenticate_scanner,
    device_detail,
    diagnostics,
    ensure_default_settings,
    get_scanner_config,
    get_settings_values,
    list_devices,
    list_events,
    list_scanners,
    overview,
    patch_scanner,
    patch_device_metadata,
    patch_settings,
    process_batch,
    record_gatt_enrichment,
    record_heartbeat,
    record_scanner_position,
    refresh_presence_states,
    refresh_scanner_states,
    register_scanner,
    scanner_config_payload,
)
from .tracking import (
    TrackingConflictError,
    TrackingNotFoundError,
    TrackingValidationError,
    get_tracking_session,
    ingest_tracking_samples,
    record_tracking_heartbeat,
    record_tracking_position,
    refresh_tracking_states,
    refresh_tracking_targets_for_scanner,
    renew_tracking_lease,
    start_tracking_session,
    stop_tracking_session,
    tracking_focus_for_scanner,
)


app = FastAPI(title="Bluetooth Scanner", version="1.0.0")
MAINTENANCE_INTERVAL_SECONDS = 5
browser_location_diagnostic_lock = Lock()
browser_location_diagnostic: dict[str, object] | None = None


def bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="scanner bearer token required")
    return authorization.split(" ", 1)[1].strip()


def tracking_http_exception(exc: ValueError) -> HTTPException:
    if isinstance(exc, TrackingNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, TrackingConflictError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def require_scanner(
    scanner_id: str,
    authorization: Annotated[Optional[str], Header()] = None,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Scanner:
    scanner = authenticate_scanner(db, scanner_id, bearer_token(authorization), settings)
    if scanner is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid scanner credentials")
    return scanner


logger = logging.getLogger("scanner_status")

def refresh_runtime_state_once(*, run_tracking_cleanup: bool = False) -> dict[str, object]:
    with SessionLocal() as db:
        settings = get_settings()
        runtime_event_count = len(refresh_presence_states(db, settings)) + len(refresh_scanner_states(db, settings))
        tracking_changes = refresh_tracking_states(db)
        cleanup = cleanup_retained_history(db, settings) if run_tracking_cleanup else None
        return {
            "runtime_event_count": runtime_event_count,
            "tracking_changes": tracking_changes,
            "cleanup": cleanup,
        }


async def refresh_runtime_states() -> None:
    last_tracking_cleanup = 0.0
    while True:
        try:
            # SQLAlchemy's synchronous SQLite session must not block the
            # asyncio event loop while the single local connection is busy.
            now = time.monotonic()
            run_cleanup = now - last_tracking_cleanup >= 3600
            result = await asyncio.to_thread(
                refresh_runtime_state_once,
                run_tracking_cleanup=run_cleanup,
            )
            if run_cleanup:
                last_tracking_cleanup = now
        except asyncio.CancelledError:
            raise
        except (OperationalError, SQLAlchemyTimeoutError) as exc:
            logger.warning("Runtime state refresh skipped while database is busy: %s", exc)
            result = {"runtime_event_count": 0, "tracking_changes": []}
        except Exception:  # noqa: BLE001
            logger.exception("Runtime state refresh failed")
            result = {"runtime_event_count": 0, "tracking_changes": []}
        events_count = int(result.get("runtime_event_count", 0))
        if events_count:
            await broker.publish("runtime_state_changed", {"event_count": events_count})
        for change in result.get("tracking_changes", []):
            await tracking_broker.publish(change["id"], "session_state", change)
        await asyncio.sleep(MAINTENANCE_INTERVAL_SECONDS)


@app.on_event("startup")
async def startup() -> None:
    broker.start()
    tracking_broker.start()
    settings = get_settings()
    if settings.dashboard_path.exists():
        app.mount("/dashboard", StaticFiles(directory=settings.dashboard_path, html=True), name="dashboard")
    app.state.runtime_refresh_task = asyncio.create_task(refresh_runtime_states())
    logger.info("Server ready. Waiting for scanner data from USB serial bridge or network scanner.")


@app.on_event("shutdown")
async def shutdown() -> None:
    broker.request_shutdown()
    tracking_broker.request_shutdown()
    task = getattr(app.state, "runtime_refresh_task", None)
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/dashboard/")

@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/runtime-config")
def runtime_config(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    return {
        "app_timezone": settings.app_timezone,
        "local_scanner_id": settings.local_scanner_id,
    }

@app.post("/api/scanners/register", response_model=ScannerRegistrationOut)
def register(
    payload: ScannerRegistrationIn,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    if settings.scanner_registration_secret:
        supplied = request.headers.get("X-Registration-Secret")
        if supplied != settings.scanner_registration_secret:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="registration secret required")

    scanner, token, config = register_scanner(db, payload, settings)
    return {
        "scanner_id": scanner.id,
        "token": token,
        "config_version": config.version,
        "config": scanner_config_payload(config),
    }

@app.post("/api/scanners/{scanner_id}/heartbeat")
def heartbeat(
    payload: HeartbeatIn,
    background_tasks: BackgroundTasks,
    scanner: Scanner = Depends(require_scanner),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = record_heartbeat(db, scanner, payload)
    tracking_state = record_tracking_heartbeat(db, scanner, payload.health)
    background_tasks.add_task(
        broker.publish,
        "scanner_heartbeat",
        {"scanner_id": scanner.id, **result},
    )
    if tracking_state is not None:
        background_tasks.add_task(
            tracking_broker.publish,
            tracking_state["session_id"],
            "session_state",
            tracking_state,
        )
    logger.info("Scanner %s connected successfully.", scanner.id)
    return result

@app.get("/api/scanners/{scanner_id}/config")
def scanner_config(
    scanner: Scanner = Depends(require_scanner),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    config = get_scanner_config(db, scanner.id)
    return scanner_config_payload(config, tracking_focus_for_scanner(db, scanner.id))

@app.post("/api/scanners/{scanner_id}/observations/batch")
def observations_batch(
    payload: ObservationBatchIn,
    background_tasks: BackgroundTasks,
    scanner: Scanner = Depends(require_scanner),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = process_batch(db, scanner, payload)
    tracking_changes = refresh_tracking_targets_for_scanner(db, scanner.id)
    background_tasks.add_task(
        broker.publish,
        "observations_ingested",
        {"scanner_id": scanner.id, **result},
    )
    for change in tracking_changes:
        background_tasks.add_task(
            tracking_broker.publish,
            change["id"],
            "session_state",
            change,
        )
    logger.debug("Received batch of %d observations from scanner %s.", len(payload.observations), scanner.id)
    return result


@app.post("/api/scanners/{scanner_id}/enrichments")
def scanner_gatt_enrichment(
    payload: GATTEnrichmentReportIn,
    background_tasks: BackgroundTasks,
    scanner: Scanner = Depends(require_scanner),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        result = record_gatt_enrichment(db, scanner, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="source observation has not been stored yet",
        )
    background_tasks.add_task(
        broker.publish,
        "device_enrichment_recorded",
        {"scanner_id": scanner.id, **result},
    )
    return result


@app.get("/api/overview")
def api_overview(
    db: Session = Depends(get_db),
) -> dict[str, object]:
    return overview(db)


@app.get("/api/devices")
def api_devices(
    status_filter: Annotated[Optional[str], Query(alias="status")] = None,
    scanner_id: Optional[str] = None,
    include_ignored: bool = False,
    include_expired: bool = False,
    include_transient: bool = True,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return list_devices(
        db,
        status=status_filter,
        scanner_id=scanner_id,
        include_ignored=include_ignored,
        include_expired=include_expired,
        include_transient=include_transient,
    )


@app.get("/api/devices/{device_id}")
def api_device_detail(
    device_id: str,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    detail = device_detail(db, device_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    return detail


@app.patch("/api/devices/{device_id}")
def api_patch_device(
    device_id: str,
    payload: DevicePatchIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = patch_device_metadata(db, device_id, payload)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    background_tasks.add_task(
        broker.publish,
        "device_metadata_updated",
        {"device_id": device_id},
    )
    return result


@app.post("/api/devices/{device_id}/tracking-sessions")
def api_start_tracking_session(
    device_id: str,
    payload: TrackingSessionCreateIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        session = start_tracking_session(db, device_id, payload)
    except (TrackingNotFoundError, TrackingConflictError, TrackingValidationError) as exc:
        raise tracking_http_exception(exc) from exc
    background_tasks.add_task(
        tracking_broker.publish,
        session["id"],
        "session_state",
        session,
    )
    background_tasks.add_task(
        broker.publish,
        "device_tracking_changed",
        {"session_id": session["id"], "device_id": device_id, "state": session["state"]},
    )
    return session


@app.get("/api/tracking-sessions/{session_id}")
def api_tracking_session(
    session_id: str,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        return get_tracking_session(db, session_id)
    except TrackingNotFoundError as exc:
        raise tracking_http_exception(exc) from exc


@app.post("/api/tracking-sessions/{session_id}/lease")
def api_renew_tracking_lease(
    session_id: str,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        session = renew_tracking_lease(db, session_id)
    except (TrackingNotFoundError, TrackingConflictError) as exc:
        raise tracking_http_exception(exc) from exc
    return session


@app.delete("/api/tracking-sessions/{session_id}")
def api_stop_tracking_session(
    session_id: str,
    background_tasks: BackgroundTasks,
    reason: str = Query(default="operator_stopped", max_length=120),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        session = stop_tracking_session(db, session_id, reason)
    except TrackingNotFoundError as exc:
        raise tracking_http_exception(exc) from exc
    background_tasks.add_task(
        tracking_broker.publish,
        session_id,
        "session_state",
        session,
    )
    background_tasks.add_task(
        broker.publish,
        "device_tracking_changed",
        {
            "session_id": session_id,
            "device_id": session["logical_device_id"],
            "state": session["state"],
        },
    )
    return session


@app.post("/api/tracking-sessions/{session_id}/positions")
def api_tracking_position(
    session_id: str,
    payload: TrackingPositionIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        position = record_tracking_position(db, session_id, payload)
    except (TrackingNotFoundError, TrackingConflictError) as exc:
        raise tracking_http_exception(exc) from exc
    background_tasks.add_task(
        tracking_broker.publish,
        session_id,
        "scanner_position",
        position,
    )
    return position


@app.get("/api/tracking-sessions/{session_id}/events")
async def api_tracking_events(
    session_id: str,
) -> StreamingResponse:
    with SessionLocal() as db:
        try:
            get_tracking_session(db, session_id, include_history=False)
        except TrackingNotFoundError as exc:
            raise tracking_http_exception(exc) from exc
    return StreamingResponse(
        tracking_broker.stream(session_id),
        media_type="text/event-stream",
    )


@app.post("/api/scanners/{scanner_id}/tracking-samples/batch")
def api_tracking_samples(
    payload: TrackingSampleBatchIn,
    background_tasks: BackgroundTasks,
    scanner: Scanner = Depends(require_scanner),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        result = ingest_tracking_samples(db, scanner, payload)
    except (TrackingNotFoundError, TrackingConflictError, TrackingValidationError) as exc:
        raise tracking_http_exception(exc) from exc
    for sample in result.pop("live_samples"):
        background_tasks.add_task(
            tracking_broker.publish,
            payload.session_id,
            "tracking_sample",
            sample,
        )
    return result


@app.post("/api/devices/correlation")
def api_manual_correlation(
    payload: ManualCorrelationIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    try:
        result = apply_manual_correlation(db, payload, settings)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    background_tasks.add_task(broker.publish, "device_correlation_changed", result)
    return result


@app.get("/api/scanners")
def api_scanners(
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return list_scanners(db)


@app.patch("/api/scanners/{scanner_id}")
def api_patch_scanner(
    scanner_id: str,
    payload: ScannerPatchIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    scanner = patch_scanner(db, scanner_id, payload)
    if scanner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scanner not found")
    background_tasks.add_task(broker.publish, "scanner_updated", scanner)
    return scanner


@app.post("/api/scanners/{scanner_id}/position")
def api_scanner_position(
    scanner_id: str,
    payload: ScannerPositionIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        scanner = record_scanner_position(db, scanner_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if scanner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scanner not found")
    if scanner["position_applied"]:
        background_tasks.add_task(broker.publish, "scanner_position_updated", scanner)
    return scanner


@app.get("/api/events")
def api_events(
    event_type: Optional[str] = None,
    scanner_id: Optional[str] = None,
    device_id: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return list_events(db, event_type=event_type, scanner_id=scanner_id, device_id=device_id, limit=limit)


@app.get("/api/settings")
def api_settings(db: Session = Depends(get_db)) -> dict[str, object]:
    return get_settings_values(db)


@app.patch("/api/settings")
def api_patch_settings(
    payload: SettingsPatchIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        result = patch_settings(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    background_tasks.add_task(
        broker.publish,
        "settings_updated",
        {"keys": list(payload.values.keys())},
    )
    return result


@app.get("/api/diagnostics")
def api_diagnostics(db: Session = Depends(get_db)) -> dict[str, object]:
    result = diagnostics(db)
    with browser_location_diagnostic_lock:
        result["browser_location"] = (
            dict(browser_location_diagnostic)
            if browser_location_diagnostic is not None
            else None
        )
    return result


@app.post("/api/browser/location-diagnostic")
def api_browser_location_diagnostic(
    payload: BrowserLocationDiagnosticIn,
) -> dict[str, object]:
    global browser_location_diagnostic
    diagnostic = payload.model_dump(mode="json")
    diagnostic["server_received_at"] = (
        datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    )
    with browser_location_diagnostic_lock:
        browser_location_diagnostic = diagnostic
    logger.info(
        "Browser location stage=%s secure=%s permission=%s watcher=%s error=%s",
        payload.stage,
        payload.secure_context,
        payload.permission_state,
        payload.watcher_active,
        payload.error_code,
    )
    return {"accepted": True, "diagnostic": diagnostic}


@app.get("/api/live/events")
async def live_events() -> StreamingResponse:
    return StreamingResponse(broker.stream(), media_type="text/event-stream")
