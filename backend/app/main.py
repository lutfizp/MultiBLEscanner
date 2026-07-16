from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Annotated
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError, TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .database import SessionLocal, get_db
from .models import Scanner
from .realtime import broker
from .schemas import (
    HeartbeatIn,
    ManualCorrelationIn,
    ObservationBatchIn,
    ScannerPatchIn,
    ScannerRegistrationIn,
    ScannerRegistrationOut,
    SettingsPatchIn,
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
    patch_settings,
    process_batch,
    record_heartbeat,
    refresh_presence_states,
    refresh_scanner_states,
    register_scanner,
    scanner_config_payload,
)


app = FastAPI(title="Bluetooth Scanner", version="1.0.0")
MAINTENANCE_INTERVAL_SECONDS = 5


def bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="scanner bearer token required")
    return authorization.split(" ", 1)[1].strip()


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


import logging

logger = logging.getLogger("scanner_status")
logger.setLevel(logging.INFO)
# (ensure basic config is set if not already by uvicorn)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('%(levelname)s:     %(message)s'))
    logger.addHandler(ch)

def refresh_runtime_state_once() -> int:
    with SessionLocal() as db:
        settings = get_settings()
        return len(refresh_presence_states(db, settings)) + len(refresh_scanner_states(db, settings))


async def refresh_runtime_states() -> None:
    while True:
        try:
            # SQLAlchemy's synchronous SQLite session must not block the
            # asyncio event loop while the single local connection is busy.
            events_count = await asyncio.to_thread(refresh_runtime_state_once)
        except asyncio.CancelledError:
            raise
        except (OperationalError, SQLAlchemyTimeoutError) as exc:
            logger.warning("Runtime state refresh skipped while database is busy: %s", exc)
            events_count = 0
        except Exception:  # noqa: BLE001
            logger.exception("Runtime state refresh failed")
            events_count = 0
        if events_count:
            await broker.publish("runtime_state_changed", {"event_count": events_count})
        await asyncio.sleep(MAINTENANCE_INTERVAL_SECONDS)


@app.on_event("startup")
async def startup() -> None:
    broker.start()
    settings = get_settings()
    if settings.dashboard_path.exists():
        app.mount("/dashboard", StaticFiles(directory=settings.dashboard_path, html=True), name="dashboard")
    app.state.runtime_refresh_task = asyncio.create_task(refresh_runtime_states())
    logger.info("Server ready. Waiting for scanner data from USB serial bridge or network scanner.")


@app.on_event("shutdown")
async def shutdown() -> None:
    broker.request_shutdown()
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
    return {"app_timezone": settings.app_timezone}

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
async def heartbeat(
    payload: HeartbeatIn,
    scanner: Scanner = Depends(require_scanner),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = record_heartbeat(db, scanner, payload)
    await broker.publish("scanner_heartbeat", {"scanner_id": scanner.id, **result})
    logger.info("Scanner %s connected successfully.", scanner.id)
    return result

@app.get("/api/scanners/{scanner_id}/config")
def scanner_config(
    scanner: Scanner = Depends(require_scanner),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    config = get_scanner_config(db, scanner.id)
    return scanner_config_payload(config)

@app.post("/api/scanners/{scanner_id}/observations/batch")
async def observations_batch(
    payload: ObservationBatchIn,
    scanner: Scanner = Depends(require_scanner),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    result = process_batch(db, scanner, payload)
    await broker.publish("observations_ingested", {"scanner_id": scanner.id, **result})
    logger.info("Received batch of %d observations from scanner %s.", len(payload.observations), scanner.id)
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
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return list_devices(
        db,
        status=status_filter,
        scanner_id=scanner_id,
        include_ignored=include_ignored,
        include_expired=include_expired,
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


@app.post("/api/devices/correlation")
async def api_manual_correlation(
    payload: ManualCorrelationIn,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    try:
        result = apply_manual_correlation(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await broker.publish("device_correlation_changed", result)
    return result


@app.get("/api/scanners")
def api_scanners(
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return list_scanners(db)


@app.patch("/api/scanners/{scanner_id}")
async def api_patch_scanner(
    scanner_id: str,
    payload: ScannerPatchIn,
    db: Session = Depends(get_db),
) -> dict[str, object]:
    scanner = patch_scanner(db, scanner_id, payload)
    if scanner is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="scanner not found")
    await broker.publish("scanner_updated", scanner)
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
async def api_patch_settings(payload: SettingsPatchIn, db: Session = Depends(get_db)) -> dict[str, object]:
    result = patch_settings(db, payload)
    await broker.publish("settings_updated", {"keys": list(payload.values.keys())})
    return result


@app.get("/api/diagnostics")
def api_diagnostics(db: Session = Depends(get_db)) -> dict[str, object]:
    return diagnostics(db)


@app.get("/api/live/events")
async def live_events() -> StreamingResponse:
    return StreamingResponse(broker.stream(), media_type="text/event-stream")
