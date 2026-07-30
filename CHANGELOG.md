# Changelog

## Phase 1: Requirement Analysis

Changed:

- Added `docs/phase-1-requirement-analysis.md`.
- Documented BLE fields that can be collected, single-scanner limitations, randomized-address risk, movement/proximity limits, expected data volume, and recommended approach.
- Read all initially available Markdown files: `Agents.md`, `BuildFlow.md`, and `Coderules.md`.
- Read later-detected reference Markdown under `refrence/MetaRadar` and `refrence/go-haystack`.

Validation:

- Confirmed the design does not claim exact indoor coordinates from one ESP32.
- Confirmed raw observed identity and inferred logical device identity are treated separately.

## Phase 2: Technical Design

Changed:

- Added `docs/phase-2-technical-design.md`.
- Added `docs/api.md`, `docs/calibration.md`, `docs/privacy.md`, and `docs/operations.md`.
- Selected a lightweight dashboard approach: native HTML/CSS/JS served by FastAPI.
- Selected Server-Sent Events for live dashboard updates.
- Defined scanner registration, token auth, heartbeat, config sync, batch ingestion, idempotency, offline sync, retention, presence, movement, and correlation strategy.

Validation:

- Design keeps dashboard login out of scope while preserving scanner device-token security.
- Design keeps future multi-scanner fields in the schema from the start.

## Phase 3: Project Structure

Changed:

- Added `docs/project-structure.md`.
- Added root setup files: `README.md`, `.env.example`, `.gitignore`, `requirements.txt`, and `docker-compose.yml`.
- Created main directories: `backend/`, `dashboard/`, `firmware/`, `simulator/`, `tests/`, and `docs/`.

Validation:

- Confirmed project structure separates backend, dashboard, firmware, simulator, tests, and docs.
- Kept dashboard framework-free and backend-centric as requested.

## Phase 4: Implementation Core

Changed:

- Added FastAPI backend modules:
  - `backend/app/main.py`
  - `backend/app/models.py`
  - `backend/app/schemas.py`
  - `backend/app/services.py`
  - `backend/app/processing.py`
  - `backend/app/security.py`
  - `backend/app/realtime.py`
  - `backend/app/config.py`
  - `backend/app/database.py`
- Implemented scanner registration, scanner token hashing, heartbeat, config fetch, idempotent batch ingestion, raw observations, observed identities, logical devices, movement/proximity processing, status events, diagnostics, and SSE.
- Added ESP32 PlatformIO firmware in `firmware/` using NimBLE, bounded RAM queue, heartbeat, config fetch, retry upload, batch upload, and dropped observation reporting.
- Added `simulator/simulator.py` for scanner simulation without external Python dependencies.

Validation:

- `python3 -m compileall backend simulator tests` passed with bytecode cache redirected to `/private/tmp`.
- Local backend health endpoint returned `{"status":"ok"}`.
- End-to-end simulator run accepted 9 observations and rejected duplicate retry as duplicates.

## Phase 5: Database and Initial Data

Changed:

- Added Alembic setup:
  - `backend/alembic.ini`
  - `backend/migrations/env.py`
  - `backend/migrations/script.py.mako`
  - `backend/migrations/versions/0001_initial_schema.py`
- Added normalized SQLAlchemy models for scanners, scanner configuration, heartbeats, locations, observed identities, logical devices, observations, location estimates, events, manual correlation decisions, settings, and processing errors.
- Added `backend/app/seed.py` for development-only initial settings, sample location, and sample scanner.

Validation:

- Ran migration successfully against temporary SQLite database at `/private/tmp/bluetooth_scanner_dev2.sqlite`.
- Ran development seed successfully and generated scanner `scn_dev_lab_001`.
- Verified diagnostics showed database status `ok`.

## Phase 6: Dashboard

Changed:

- Added native dashboard files:
  - `dashboard/index.html`
  - `dashboard/app.css`
  - `dashboard/app.js`
- Implemented overview metrics, live device table, device detail panel, signal chart, scanner management form, location/proximity view, event timeline, settings form, diagnostics panel, SSE reconnect, and REST refresh.
- Dashboard uses real backend endpoints, not static mock data.

Validation:

- `GET /dashboard/` returned HTTP 200.
- `GET /api/overview`, `GET /api/devices`, and `GET /api/diagnostics` returned live data after simulator ingestion.

## Phase 7: Testing

Changed:

- Added `tests/test_processing.py`.
- Added `docs/testing.md`.
- Tests cover distance/proximity, RSSI smoothing, movement classification, missing/offline/return thresholds, randomized-address caution, identity scoring, hex validation, and UTC normalization.
- Simulator supports stationary, moving, disappearing, returning, randomized, noisy, delayed, and duplicate-message scenarios.

Validation:

- Ran `python3 -m pytest tests`.
- Result: 8 passed.
- PlatformIO firmware compile was not run because `platformio` is not installed in PATH.

## Phase 8: Deployment and Operations

Changed:

- Added local development, production, scanner firmware, second-scanner, backup/restore, troubleshooting, privacy, calibration, and simulator documentation.
- Added Docker Compose PostgreSQL service for normal local/prod-like database setup.
- Started a local demo backend with:
  - `DATABASE_URL=sqlite:////private/tmp/bluetooth_scanner_dev2.sqlite`
  - URL: `http://127.0.0.1:8000/dashboard/`

Validation:

- Uvicorn is running locally on `http://127.0.0.1:8000`.
- Local demo data contains one development scanner and three simulated BLE devices.
- Diagnostics after simulator validation: 9 observations, 0 processing errors, 1 online scanner.

## Update: Indonesia Map And One-Command Runner

Changed:

- Added `.env` for ready local SQLite development.
- Added `run.py` so the app can be started with `python3 run.py`.
- Updated `backend/app/config.py` to ignore runner-only `.env` keys.
- Updated `backend/app/seed.py` to use stable `DEV_SCANNER_TOKEN` when present.
- Updated scanner API serialization to include latitude, longitude, indoor coordinates, and orientation.
- Replaced the old Location View with an offline Indonesia map and local BLE coverage radar in `dashboard/index.html`, `dashboard/app.css`, and `dashboard/app.js`.
- Added browser geolocation flow from Locations and Scanner Management to store scanner coordinates automatically after permission.
- Updated `README.md` and `docs/operations.md` with one-command run and scanner-location detection instructions.

Validation:

- `python3 -m compileall backend simulator tests run.py` passed.
- `python3 -m pytest tests` passed: 8 tests.
- `node --check dashboard/app.js` passed.
- `python3 run.py` started the app on `http://127.0.0.1:8000`.
- `GET /dashboard/` returned HTTP 200.
- Simulator with `local-dev-scanner-token` ingested 6 observations with 0 processing errors.

Notes:

- The ESP32 has no GPS by default, so automatic scanner location uses browser geolocation from the dashboard.
- With one scanner, Bluetooth device points are proximity estimates around the scanner location, not exact device coordinates.

## Update: Reference-Based BLE Intelligence

Changed:

- Re-read relevant reference code in `refrence/MetaRadar` and `refrence/go-haystack`.
- Added `backend/app/data/bluetooth_sig_companies.json` from the MetaRadar Bluetooth SIG company table.
- Added `backend/app/bluetooth_sig.py` for Bluetooth SIG Company Identifier parsing and lookup.
- Added `backend/app/device_intelligence.py` for manufacturer profile analysis, service/name-based category inference, Apple Find My payload recognition, and Apple Nearby/AirDrop-style payload recognition.
- Updated backend ingestion so logical devices receive inferred vendor and category values.
- Updated device detail responses with manufacturer profile data.
- Updated dashboard device table/detail to show vendor, category, Bluetooth SIG company ID/name, and Find My/AirDrop-style metadata.
- Added `docs/reference-comparison.md`.

Validation:

- `python3 -m compileall backend simulator tests run.py` passed.
- `python3 -m pytest tests` passed: 11 tests.
- `node --check dashboard/app.js` passed.
- Simulator validation succeeded with idempotent duplicate handling.
- `GET /api/devices` shows enriched vendor/category data.

Notes:

- MetaRadar's active GATT deep analysis was reviewed but not enabled by default because it can increase scan time, power usage, pairing prompts, and scanner visibility.
- Go Haystack's Find My network integration was reviewed but not adopted because this project is a local BLE scanner, not a Find My client.

## Update: Real Map And Live Development Mode

Changed:

- Downloaded Leaflet 1.9.4 locally into `dashboard/vendor/leaflet`.
- Replaced the rough SVG Indonesia map with a real Leaflet/OpenStreetMap map.
- Added auto-zoom to the selected scanner's stored coordinates.
- Added BLE scan markers and uncertainty circles around the scanner location.
- Added OpenStreetMap/Nominatim reverse geocoding panel for jalan/area, kelurahan/desa, kecamatan/distrik, kota/kabupaten, province, postcode, and full OSM address.
- Added `START_DEV_SIMULATOR=true` and `DEV_SIMULATOR_INTERVAL=5` to local `.env`.
- Updated `run.py` to auto-start a background development simulator so overview does not immediately become offline/missing when no ESP32 is running.

Validation:

- Leaflet CSS, JS, and marker images are present locally.
- Map endpoint `GET /dashboard/` serves successfully.
- Development simulator keeps scanner heartbeat and scan observations live during `python3 run.py`.

Notes:

- OpenStreetMap map tiles and Nominatim address lookup require browser internet access.
- Bluetooth scan result markers are proximity estimates around the scanner. Exact GPS for scanned BLE devices still requires more location sources than one ESP32 scanner can provide.

## Update: Dashboard State Stability

Changed:

- Hardened `DeviceEvent` dedupe handling so repeated offline/missing transitions in the same refresh cycle do not create duplicate pending events.
- Added commit handling for status refresh race conditions, preventing `/api/overview` and scanner listing requests from failing when parallel dashboard refreshes try to create the same deduped event.
- Added a regression test for duplicate pending event creation.

Validation:

- `PYTHONPYCACHEPREFIX=/private/tmp/bluetooth_scanner_pycache python3 -m compileall backend simulator tests run.py` passed.
- `python3 -m pytest tests` passed: 12 tests.
- `node --check dashboard/app.js` passed.

## Update: Real ESP32 USB Serial Runner

Changed:

- Audited the unchangeloged local ESP32 serial work: `serial_bridge.py`, `read_serial.py`, `diagnose_esp32.py`, and `firmware/src/main.cpp`.
- Reworked `run.py` so `python3 run.py` starts FastAPI plus the real ESP32 USB serial bridge, with no simulator process.
- Added serial auto-detection for ESP32-like ports, backend forwarding through the actual chosen API port, and retry behavior when the ESP32 is not ready yet.
- Added runner port fallback when 8000 is temporarily busy from stale browser/Code connections.
- Added `.env` keys for the local USB scanner, serial bridge, and simulator-data purge.
- Added `pyserial` to `requirements.txt`.
- Updated backend schemas to accept empty datetime strings emitted by ESP32 serial firmware when no NTP clock is available.
- Added startup cleanup for old simulator observations, heartbeats, events, location estimates, and orphaned dummy logical devices when `PURGE_SIMULATOR_DATA=true`.
- Updated scanner seeding labels from development/demo wording to local USB ESP32 wording.
- Updated `README.md` and `docs/operations.md` for no-dummy USB serial operation.

Validation:

- `python3 read_serial.py` confirmed the connected ESP32 emits `|||BRIDGE_START|||` frames for heartbeat and observation batches.
- `python3 run.py` is running on `http://127.0.0.1:8000/dashboard/` with the real ESP32 serial bridge.
- Real ESP32 heartbeat and multiple BLE observation batches returned HTTP 200.
- Cleanup removed 2694 simulator observations, 674 simulator heartbeats, 1272 simulator events, 2694 simulator location estimates, 1 manual simulator decision, 4 dummy logical devices, and 4 orphaned observed identities.
- `GET /api/overview` shows 1 online scanner, 0 offline scanners, live real BLE observations, and system health `ok`.
- `GET /api/diagnostics` shows 0 processing errors.
- `GET /dashboard/` returns HTTP 200.
- `PYTHONPYCACHEPREFIX=/private/tmp/bluetooth_scanner_pycache python3 -m compileall backend simulator tests run.py serial_bridge.py` passed.
- `python3 -m pytest tests` passed: 13 tests.
- `node --check dashboard/app.js` passed.

Notes:

- `simulator/simulator.py` remains available for optional tests, but `run.py` no longer starts it.
- Bluetooth device map markers are still proximity estimates around the scanner. Exact BLE device coordinates require more than one scanner or another location source.

## Update: Timezone And Reference Recheck

Changed:

- Stopped the running backend and ESP32 serial bridge.
- Re-read local reference markdown files and the relevant MetaRadar/go-haystack/ESP32_BLETracker location, scan, manufacturer, following, and Find My parsing code.
- Added `APP_TIMEZONE=Asia/Jakarta` and `/api/runtime-config`.
- Updated API datetime serialization so SQLite datetimes are returned as explicit UTC strings with `Z`.
- Updated dashboard date formatting to render timestamps in the configured timezone.
- Removed the Haystack exact-location lookup from normal ingestion so Find My metadata is not presented as verified BLE-device coordinates.
- Added a regression test for API datetime serialization.

Validation:

- Confirmed TCP port 8000 and `/dev/cu.usbserial-0001` are no longer held after shutdown.
- `PYTHONPYCACHEPREFIX=/private/tmp/bluetooth_scanner_pycache python3 -m compileall backend simulator tests run.py serial_bridge.py read_serial.py diagnose_esp32.py` passed.
- `python3 -m pytest tests` passed: 14 tests.
- `node --check dashboard/app.js` passed.
- SQLite audit showed all remaining observations are from real firmware version `esp32-ble-scanner-1.0.0`.

## Update: Scan Data Reset And Serial Frame Noise

Changed:

- Added `clear_scan_data()` to remove BLE scan-derived data while keeping scanner registration/config intact.
- Cleared existing scan data from SQLite: 10 logical devices, 9 observed identities, 506 observations, 506 location estimates, and 160 device events.
- Updated `serial_bridge.py` so orphan `|||BRIDGE_END|||` markers read during serial startup are ignored silently instead of spamming malformed-frame logs.

Validation:

- After clearing, new rows appeared only because the ESP32 was still running and immediately sent fresh scan batches.
- `GET /api/diagnostics` returned 0 processing errors after cleanup.

## Update: Suspicious BLE Test Address Filtering

Changed:

- Added conservative backend detection for known non-real/test BLE address patterns such as `11:22:33:44:55:66`, all-zero, all-`ff`, and repeated-octet placeholder addresses.
- Updated observation ingestion so those test-pattern addresses are ignored before identity, logical-device, movement, or location rows are created.
- Added `ignored` count to batch ingestion results for observations rejected by this filter.
- Added `purge_suspicious_scan_data()` to remove already-ingested test-pattern scan rows while keeping scanner registration/config and other real ESP32 scan observations.
- Updated `run.py`, `.env`, and `.env.example` so startup automatically purges known BLE test-address rows with `PURGE_SUSPICIOUS_SCAN_DATA=true`.
- Updated `README.md` and `docs/operations.md` to document that placeholder BLE addresses are rejected while Apple `0x004C` advertisements are not automatically fake.
- Purged the current SQLite database of the suspicious `11:22:33:44:55:66` row. First purge removed 1 logical device, 1 observed identity, 29 observations, 29 location estimates, and 15 events.
- Purged again after the still-running old backend reinserted the placeholder row, removing 1 logical device, 1 observed identity, 7 observations, 7 location estimates, and 8 events.
- Added a regression test proving placeholder addresses are rejected while normal random/private-looking addresses are not automatically treated as fake.

Validation:

- SQLite audit before cleanup found `11:22:33:44:55:66` as the only clear test-pattern logical device.
- SQLite audit after both cleanups found no remaining logical devices with suspicious test-pattern addresses.
- `PYTHONPYCACHEPREFIX=/private/tmp/bluetooth_scanner_pycache python3 -m compileall backend simulator tests run.py serial_bridge.py read_serial.py diagnose_esp32.py` passed.
- `python3 -m pytest tests` passed: 15 tests.
- `node --check dashboard/app.js` passed.

## Update: Remove Runtime Generated Scanner Feed

Changed:

- Deleted `simulator/simulator.py` and removed the now-empty `simulator/` directory.
- Removed runner startup paths for simulator-data purge and suspicious-data purge env flags from `run.py`.
- Removed `PURGE_SIMULATOR_DATA` and `PURGE_SUSPICIOUS_SCAN_DATA` from `.env` and `.env.example`.
- Removed simulator cleanup code and simulator firmware markers from `backend/app/seed.py`.
- Renamed the local scanner bootstrap from `seed_development_data()` to `ensure_local_scanner()`.
- Removed `DEV_SCANNER_TOKEN` fallback from `.env`, `backend/app/seed.py`, and `serial_bridge.py`; the real local path now uses `LOCAL_SCANNER_TOKEN`.
- Replaced hard-coded test-address literals in `backend/app/processing.py` with structural synthetic-address detection.
- Updated ingestion rejection reason from `test_address_pattern` to `synthetic_address_pattern`.
- Removed simulator instructions from `README.md`, `docs/testing.md`, `docs/project-structure.md`, `docs/phase-2-technical-design.md`, and `docs/operations.md`.
- Updated `BuildFlow.md` and `Coderules.md` so future work targets the real ESP32 USB serial path, not generated scan feeds.
- Updated tests so they no longer store the previously reported placeholder MAC address literal.

Validation:

- `rg --files | rg '^simulator/'` returned no files.
- Active project docs/code search found no simulator, generated-test-feed, dummy, incomplete-stand-in, or previous placeholder MAC-address literals outside historical changelog/reference material.
- `PYTHONPYCACHEPREFIX=/private/tmp/bluetooth_scanner_pycache python3 -m compileall backend tests run.py serial_bridge.py read_serial.py diagnose_esp32.py test_esp32.py` passed.
- `python3 -m pytest tests` passed: 15 tests.
- `node --check dashboard/app.js` passed.
- SQLite audit showed 10 logical devices, 997 observations, 9 observed identities, and 0 synthetic-address-pattern devices.

## Update: RSSI Distance Reference Guard

Changed:

- Updated RSSI distance estimation so positive or zero BLE `tx_power` advertisement values are not treated as calibrated RSSI-at-1m references.
- Kept negative calibrated reference values valid when they are in a plausible RSSI range.
- Added a regression test for the observed case where `tx_power=12` incorrectly pushed a `-75 dBm` observation to the 100 m clamp.

Validation:

- `PYTHONPYCACHEPREFIX=/private/tmp/bluetooth_scanner_pycache python3 -m compileall backend tests run.py serial_bridge.py read_serial.py diagnose_esp32.py test_esp32.py` passed.
- `python3 -m pytest tests` passed: 16 tests.
- `node --check dashboard/app.js` passed.

## Update: Probabilistic Single-Scanner Proximity

Changed:

- Added a single-scanner Bayesian log-distance proximity model that converts smoothed RSSI into distance bands, approximate median distance, 10-90% distance range, band probabilities, and confidence.
- Kept positive or zero BLE `tx_power` values ignored as distance references inside the new proximity model.
- Stored the proximity model output in observation processing notes and device location estimate details so future UI/API changes can use the original inference metadata.
- Updated device API serialization to expose `distance_range_m`, `proximity_confidence`, and `proximity_model`.
- Updated the dashboard device table, detail panel, popups, and scanner coverage list to display probabilistic proximity ranges instead of a single overconfident meter value.
- Added a band-probability view in device details.
- Removed the unused Haystack exact-location helper from active backend code.
- Replaced the remaining map marker label that implied Find My exact location with pinned external coordinates.
- Added a regression test for probabilistic proximity output around the observed `-75 dBm` / positive `tx_power` case.

Validation:

- `PYTHONPYCACHEPREFIX=/private/tmp/bluetooth_scanner_pycache python3 -m compileall backend tests run.py serial_bridge.py read_serial.py diagnose_esp32.py test_esp32.py` passed.
- `python3 -m pytest tests` passed: 17 tests.
- `node --check dashboard/app.js` passed.
- Active code search found no `haystack_client`, exact-location helper, simulator, dummy/fake marker, or previous placeholder MAC literal outside excluded historical/reference material.

## Update: Scanner Liveness And Location Anchoring

Changed:

- Updated scanner offline detection so a fresh observation batch also counts as scanner liveness, not only heartbeat packets.
- Added a `scanner_connected` event when a scanner returns through observation batches after being non-online.
- Added location-aware logical-device matching so the same BLE identity/address seen at a substantially different scanner coordinate creates a new anchored logical-device record instead of moving the old location record.
- Kept existing logical-device latitude/longitude as the map anchor once set; later scans only fill missing coordinates, they do not overwrite an existing anchor.
- Added scanner/device coordinate metadata into device-location estimate details for later diagnostics.
- Updated Location View filtering so devices with stored coordinates belong to the selected scanner location by coordinate proximity, not by the latest `current_scanner_id` alone.
- Tightened ESP32 serial auto-detection so non-ESP32 ports such as macOS debug console are not selected when no ESP32 USB serial device is visible.
- Reworked `read_serial.py` to use the same auto-detection logic as `serial_bridge.py` instead of a hard-coded `/dev/cu.usbserial-0001` port.
- Added regression tests for stale heartbeat with fresh scanner liveness and for preserving old location anchors when the same BLE identity appears at a new scanner location.

Validation:

- Current local API was not running on port 8000 during inspection.
- SQLite showed scanner `scn_dev_lab_001` offline because the latest stored heartbeat was `2026-07-10 10:57:39` and latest stored observation was `2026-07-10 10:57:44`.
- Current serial-port inspection found no ESP32-like USB serial device; visible ports were only `/dev/cu.debug-console`, `/dev/cu.Bluetooth-Incoming-Port`, and `/dev/cu.MoondropSpaceTravel`.
- `python3 serial_bridge.py --port auto --no-retry --timeout 2` now reports no ESP32-like serial port instead of trying `/dev/cu.debug-console`.
- `python3 read_serial.py --seconds 1` now reports no ESP32-like serial port with the visible-port list.
- `PYTHONPYCACHEPREFIX=/private/tmp/bluetooth_scanner_pycache python3 -m compileall backend tests run.py serial_bridge.py read_serial.py diagnose_esp32.py test_esp32.py` passed.
- `python3 -m pytest tests` passed: 21 tests.
- `node --check dashboard/app.js` passed.
- Active code search found no simulator, dummy/fake marker, previous placeholder MAC literal, or Haystack exact-location helper outside excluded historical/reference material.

## Update: USB Serial Detection Diagnosis

Changed:

- Updated `diagnose_esp32.py` to use the same ESP32 port auto-detection as `run.py` and `serial_bridge.py`; it no longer assumes the obsolete `/dev/cu.usbserial-0001` path.
- The diagnostic now reports whether the serial port is unavailable versus merely failing to open, without touching scanner data or emitting synthetic observations.

Validation:

- macOS USB inspection found no ESP32 USB device and no ESP32-like serial port. The only visible serial endpoints were macOS Bluetooth, an audio device, and the debug console.
- The installed firmware initializes `Serial` at `115200` and emits bridge frames, matching the Python bridge configuration.
- `python3 serial_bridge.py --port auto --no-retry --timeout 2` reports the absence of an ESP32-like port correctly.

## Update: Real ESP32 USB Connection Verified

Validation:

- macOS now detects the scanner as `/dev/cu.usbserial-0001` (`CP2102 USB to UART Bridge Controller`, VID:PID `10C4:EA60`).
- Raw serial inspection confirmed a real ESP32 boot sequence, a heartbeat for `scn_dev_lab_001`, and a BLE observation batch emitted in the expected USB bridge-frame format.
- The batch reported `dropped_observations: 0` and contained real nearby BLE advertisements; no simulator or generated observation source was involved.
- Starting the backend is currently blocked only by the existing root-owned SQLite file requiring an interactive `sudo` password. This is independent of ESP32 detection and firmware operation.

## Update: Clean Real-Scan Baseline

Changed:

- Extended scan-data reset to clear device events, location estimates, observations, processing errors, manual correlation decisions, observed identities, logical devices, and scanner heartbeat history.
- Reset only scanner runtime state (`status`, last-seen/heartbeat values, uptime, network state); scanner registration, token, physical location, calibration, and configuration remain intact.
- Added a regression test proving a synthetic address pattern is ignored before any observation, observed identity, or logical device can be stored.
- Added a regression test proving scan-data reset keeps scanner configuration while clearing its runtime state.

Validation:

- Removed the previously collected runtime data: 1,044 device events, 11,122 location estimates, 11,122 observations, 70 heartbeats, 51 logical devices, and 46 observed identities across the two cleanup passes. No scanner configuration or location record was deleted.
- Verified all dynamic scan tables now contain `0` rows; scanner `scn_dev_lab_001` remains registered with no last heartbeat or last seen timestamp.
- Confirmed no backend or serial bridge process is running, so no new observation can enter before the next intentional `python3 run.py` start.
- Vacuumed SQLite after reset; `bluetooth_scanner.sqlite3` reduced to 184 KB.
- `python3 -m pytest tests` passed: 23 tests.
- `PYTHONPYCACHEPREFIX=/private/tmp/bluetooth_scanner_pycache python3 -m compileall backend/app/seed.py tests/test_processing.py` passed.

## Update: Current Device Location Follows Scanner Movement

Changed:

- Removed the normal-map prompt that described automatic GPS tracking and removed an unintended boolean value from device map popups.
- Changed direct device correlation so an observation with the same BLE address continues the same logical-device record across different scanners and updates its current scanner, zone, and stored coordinates to the newest scanner location.
- Preserved `DeviceLocationEstimate` rows as location history and added that history to device-detail API responses and the dashboard device detail panel.
- Added `device_location_changed` events containing previous/current scanner, zone, and scanner coordinates whenever the current device location changes.
- Prevented delayed observations from overwriting a newer active device location; they are still stored as historical observations and estimates.
- Kept cross-location matching cautious for observations without an address or only an advertisement-pattern match, so ambiguous randomized BLE broadcasts cannot move a device record incorrectly.
- Updated Location View filtering to use the device's current scanner assignment rather than a stale coordinate anchor.

Validation:

- Regression test covers Scanner A to Scanner B movement for one BLE address, preserves both locations in history, and confirms a delayed Scanner A observation cannot revert the current Scanner B location.
- `python3 -m pytest tests` passed: 23 tests.
- `PYTHONPYCACHEPREFIX=/private/tmp/bluetooth_scanner_pycache python3 -m compileall backend/app/services.py tests/test_processing.py` passed.
- `node --check dashboard/app.js` passed.

## Phase: Verified Active Scan, AD Parsing, And Time Provenance

Changed:

- Updated `firmware/src/main.cpp` to retain separate raw ADV and scan-response byte payloads using NimBLE's advertised-payload length, rather than storing a combined payload as ADV.
- Kept active scanning enabled and added exact advertising PDU/address-type labels, packet lengths, scan-cycle sequence, boot ID, and monotonic milliseconds to real ESP32 observations.
- Removed unused Wi-Fi/backend credentials from `firmware/include/config.h`; local USB transport now depends on the Python bridge and `.env` only.
- Updated `serial_bridge.py` to provide host UTC time to the ESP32 through USB at connection and every 60 seconds. The firmware reports its synchronization state and age with every batch.
- Added `backend/app/bluetooth_ad.py`, which parses every captured AD structure while preserving raw bytes, including flags, local names, UUID lists, service data, manufacturer data, Tx Power, appearance, unknown types, and parse errors.
- Updated backend schema and ingestion to validate byte-aligned raw payloads and their declared ADV/scan-response lengths. For layout v2, parsed fields are canonical only when derived from the captured raw bytes.
- Added time provenance: synchronized scanner time is accepted only with fresh synchronization metadata and without a material future skew. Otherwise, the original scanner timestamp is retained separately and server receive time becomes the effective ordering time.
- Removed automatic logical-device merges based on advertisement similarity, RSSI, time, scanner location, or candidate scores. Different randomized addresses remain separate unless an exact raw observed identity is present or an operator merges them manually.
- Updated device details to show radio-identity basis, capture verification state, and timestamp provenance instead of an ambiguous identity-confidence percentage.
- Updated API, architecture, operations, testing, and README documentation for the strict no-candidate policy and USB time behavior.
- Added regression coverage for ADV/scan-response separation, AD parsing, malformed structures, strict byte validation, no-candidate randomized identities, trusted USB time, stale-clock fallback, and multi-scanner delayed ordering.

Validation:

- `python3 -m pytest tests` passed: 28 tests.
- `PYTHONPYCACHEPREFIX=/private/tmp/bluetooth_scanner_pycache python3 -m compileall -q backend serial_bridge.py run.py` passed.
- `node --check dashboard/app.js` passed.
- `python3 -m platformio run --project-dir firmware` passed for `esp32dev`: Flash 46.8%, RAM 22.6%.

Limits:

- AD fields strengthen classification and retain possible static identifiers, but they do not by themselves prove that two different randomized addresses are one physical device.
- USB host time is only as correct as the connected Mac clock. The backend labels fallback ordering instead of treating stale scanner time as factual.
- GATT enrichment and IRK/RPA resolution are intentionally not enabled in this phase; both require explicit authorization and separate device-access policy.

## Phase: Correlation And RSSI Evidence Review

Changed:

- Reviewed peer-reviewed BLE random-address correlation work before changing the matching policy again:
  - Akiyama and Taniguchi (2024), `10.23919/COMEX.2023XBL0157`: global linear assignment using time cost and RSSI-regression cost.
  - Becker, Li, and Starobinski (2019), `10.2478/popets-2019-0036`: address carryover when payload tokens and addresses rotate asynchronously.
  - Locatelli et al. (2023), `10.1016/j.comcom.2023.02.008`: BLE device fingerprinting/tracing with multiple strategies.
- Reviewed BLE RSSI localization evidence showing that indoor multipath, obstacles, human orientation, and advertising channels make raw RSSI unsuitable for an uncalibrated exact-distance claim; multi-anchor calibration and filtering are required for coordinate estimates.
- Defined the next correlation direction: restore multi-evidence correlation as an auditable global assignment process, with protocol-specific payload continuity, time sequence, RSSI regression residual, and scanner-transition constraints. It will not use the old generic weighted candidate score.
- Defined the next location direction: one scanner will report calibrated radial proximity and approach/recede evidence only; exact coordinates require synchronized, calibrated multi-scanner observations and at least three usable non-collinear anchors.
- No runtime code was changed in this research phase. The strict no-candidate implementation from the prior phase remains active until its evidence-based replacement is implemented and tested.

Validation:

- Research conclusions were cross-checked against BLE privacy/correlation papers and BLE RSSI indoor-localization experiments.
- No generated observations, simulator path, or placeholder data was introduced.

## Phase: Evidence-Grounded Identity Correlation And Calibrated RSSI

Changed:

- Added `backend/app/correlation.py` with the Akiyama and Taniguchi time/RSSI cost, predecessor-window linear RSSI regression, 90th-percentile scale derivation, global minimum-cost assignment, and explicit unmatched choices.
- Added `DeviceIdentityCorrelation` plus Alembic migration `0003_identity_correlations.py`. Every proposal or accepted carryover records its method, status, time gap, RSSI residual, cost, alpha, windows, and evidence metadata.
- Added a direct accepted path only for an operator-approved, scoped AD token rule of at least 40 bits. It requires exactly one eligible predecessor and never exposes the raw token in correlation evidence.
- Kept RSSI-time assignment review-only by default. It produces an auditable proposal and cannot merge records or move a current device location until a separately validated policy is explicitly enabled with a maximum cost.
- Implemented accepted Tebet-to-Bekasi continuity: when an accepted identity appears through another scanner, historical observations and location estimates stay under one canonical record while current scanner, zone, coordinates, and current observed address advance to the newest accepted observation.
- Removed synthetic device placement from the Leaflet map and local radar. The map now shows real scanner coordinates only and lists devices as last observed by a scanner.
- Required verified scanner calibration before presenting live RSSI distance/proximity. Without it, the backend stores raw/smoothed RSSI but returns `uncalibrated_rssi_no_distance` and `signal_unclassified` instead of a physical-distance or movement claim.
- Added scanner calibration JSON editing, structured JSON/boolean system-settings editors, correlation detail in device view, `docs/correlation.md`, and revised calibration/architecture/README documentation.
- Repaired the pre-existing Alembic revision chain and made the existing dynamic initial-schema migration safe when later model columns/tables already exist.

Validation:

- Added regression tests for the paper cost calculation, safe unmatched assignment, scoped 40-bit AD token extraction, accepted cross-scanner token carryover, review-only RSSI-time proposal, and uncalibrated live distance guard.
- `PYTHONPYCACHEPREFIX=/private/tmp/bluetooth_scanner_pycache python3 -m pytest tests` passed: 35 tests.
- `PYTHONPYCACHEPREFIX=/private/tmp/bluetooth_scanner_pycache python3 -m compileall -q backend tests run.py serial_bridge.py read_serial.py diagnose_esp32.py test_esp32.py` passed.
- `node --check dashboard/app.js` passed.
- `DATABASE_URL=sqlite:////private/tmp/bluetooth_scanner_correlation_migration_final.sqlite python3 -m alembic -c alembic.ini upgrade head` completed through `0003`.

Limits:

- An approved AD token is only as strong as the operator's protocol-specific evidence that it is stable and unique. It is not inferred from vendor, name, UUID, RSSI, location, or ordinary advertising similarity.
- The RSSI-time cost is not an identity probability and is not validated for this ESP32 deployment by the paper's reported experiment. It remains a review proposal until local labelled validation establishes an acceptable false-link rate.
- One scanner cannot determine device direction or a geographic device coordinate from RSSI. Exact multi-scanner positioning remains a future calibrated deployment capability.

## Update: Uncalibrated Signal Presentation And Status Markers

Changed:

- Fixed the dashboard null-number check that rendered uncalibrated distance as `unknown - m · 0%`.
- Uncalibrated observations now display `not calibrated` with a direct radio-strength label such as `RSSI weak; distance unavailable`, rather than a fabricated meter value.
- Added online, temporary-missing, and offline status dots to the scanner-local device list.
- Changed real scanner map pins to green for online, red for offline/disabled, and amber for registered/unknown scanners. No device coordinate is added to the map from one scanner's RSSI.
- Normalized underscore-separated state labels into readable dashboard text.

Validation:

- `node --check dashboard/app.js` passed.
- `PYTHONPYCACHEPREFIX=/private/tmp/bluetooth_scanner_pycache python3 -m pytest tests` passed: 35 tests.

## Update: Verified SIG Company Presentation

Changed:

- Stopped presenting scanner-API manufacturer data as a device vendor unless its Bluetooth SIG Company Identifier was parsed from a complete, raw ADV capture.
- Renamed the dashboard column and device detail field from `Vendor` to `SIG Company` to avoid claiming that a manufacturer-data namespace identifies the physical product manufacturer.
- Legacy ESP32 payload-layout records, incomplete raw parses, and historical records without capture provenance now withhold company names instead of displaying an unverified `Apple, Inc.` label.
- Kept all original manufacturer data and raw observations in storage for later reprocessing; only the presentation and logical-device vendor assignment are evidence-gated.
- Added regression coverage for both cases: a verified raw ADV with `0x004C` displays `Apple, Inc.`, while the same company value from the legacy scanner API remains hidden.

Validation:

- `PYTHONPYCACHEPREFIX=/private/tmp/bluetooth_scanner_pycache python3 -m pytest tests` passed: 36 tests.
- `PYTHONPYCACHEPREFIX=/private/tmp/bluetooth_scanner_pycache python3 -m compileall -q backend/app/services.py tests/test_processing.py` passed.
- `node --check dashboard/app.js` passed.
- Read-only verification against the configured real database found 212 device records, 0 displayed SIG companies, 180 legacy payload-layout records, and 32 older records without capture provenance.
- Started `python3 run.py` in real USB mode and verified the ESP32 heartbeat plus a real batch of 32 observations. The scanner is online; all 32 latest observations remain correctly marked `legacy_payload_layout_unverified`, with 0 SIG companies exposed.

Limit:

- A raw `0x004C` value will mean that the advertisement uses Apple's assigned manufacturer-data namespace. It still does not prove that the physical device itself is an Apple product.

## Phase: Calibration-Free Signal Processing And Clean Runtime Reset

Changed:

- Removed the live RSSI-to-meter Bayesian log-distance calculation, including `reference_rssi_at_1m`, `path_loss_exponent`, residual sigma, calibration verification, and distance-range output.
- Replaced it with fixed relative signal bands over EMA-smoothed RSSI: `signal_strong` (`>= -60 dBm`), `signal_moderate` (`-75..-61 dBm`), `signal_weak` (`-88..-76 dBm`), and `signal_very_weak` (`< -88 dBm`). These are radio-strength classes, not distance or room claims.
- Kept movement detection based on smoothed RSSI change and hysteresis, but renamed its reasons to make clear that it is a relative signal change. One scanner still cannot establish direction or physical movement with certainty.
- Removed calibration controls from the scanner dashboard form, scanner patch schema, active scanner serialization, and active scanner configuration payload. Legacy database columns are left unused for migration compatibility.
- Set one-scanner `location_confidence` to `0` because a scanner's own zone is not proof of the device's exact position. The dashboard now says that device coordinates are not inferred from one scanner.
- Updated README and technical/operations/testing documentation to describe the signal-band method and its limits.
- Reset runtime data before and after live verification. Scanner registration, physical location, token, and configuration were preserved.

Validation:

- `PYTHONPYCACHEPREFIX=/tmp/bluetooth-scanner-pycache python3 -m pytest` passed: 38 tests.
- `PYTHONPYCACHEPREFIX=/tmp/bluetooth-scanner-pycache python3 -m compileall -q backend run.py serial_bridge.py` passed.
- `node --check dashboard/app.js` passed.
- Controlled real-hardware check: ESP32 connected through `/dev/cu.usbserial-0001`, heartbeat and configuration returned HTTP 200, and real batches of 31, 28, and 27 observations returned HTTP 200.
- Live API output showed signal bands, `estimated_distance_m: null`, `distance_range_m: null`, `distance_available: false`, and `location_confidence: 0`.
- The server and USB bridge were stopped after verification. Final runtime counts are zero for observations, observed identities, logical devices, location estimates, events, heartbeats, and processing errors. Scanner `scn_dev_lab_001` remains registered.

Limits:

- This method intentionally does not answer “how many meters” or “which room” from one ESP32. Research on BLE proximity supports sequence filtering and classification, but absolute distance remains dependent on transmitter, orientation, antenna, and environment; a universal meter formula would be misleading.
- A Bluetooth Classic-only TWS can still be absent because the current firmware discovers BLE advertisements. That is a separate scanning capability from this signal-processing change.

## Phase: Real ESP32 Transport And SQLite Reliability

Changed:

- Configured the local SQLite engine for WAL mode, a 30-second busy timeout, foreign keys, and a single bounded connection pool. `run.py` now holds a process lock before opening the database, so a second runner cannot contend with the active scanner.
- Moved periodic presence/scanner-state refresh out of dashboard GET handlers. Dashboard reads no longer create status writes on every poll.
- Reworked live-device lookups to fetch only the latest observation and location estimate per logical device, instead of reading the complete historical tables for every dashboard refresh.
- Added a regression test for latest-per-device lookup and for cross-batch observation idempotency; scanner retries retain their original batch/observation identifiers.
- Updated the USB serial bridge and ESP32 firmware to exchange explicit HTTP acknowledgements and scanner configuration through serial. Pending batches are retained and retried with their original IDs until the backend acknowledges success.
- Built and flashed the connected ESP32 with `esp32-ble-scanner-1.2.0`. Firmware capture uses active BLE scanning and payload layout v2, preserving raw advertising and scan-response data separately.
- Kept `bluetooth_sig_companies.json` as an evidence-gated Bluetooth SIG Company Identifier lookup only. It is not used to assert a physical product vendor.
- Documented that `rssi_min=-95` is an operational capture floor, not a universal RSSI-to-distance calibration. The dashboard keeps physical distance unavailable until a scanner-specific calibration is supplied.

Validation:

- `python3 -m pytest` passed: 40 tests.
- `PYTHONPYCACHEPREFIX=/tmp/bluetooth-scanner-pycache python3 -m compileall -q backend run.py serial_bridge.py` passed.
- `node --check dashboard/app.js` passed.
- `python3 -m platformio run --project-dir firmware` passed: Flash 46.6%, RAM 22.6%.
- Live USB verification: scanner `scn_dev_lab_001` is online with firmware `esp32-ble-scanner-1.2.0`; a later heartbeat reported uptime `104` seconds, buffer `0`, pending `0`, and dropped observations `0`.
- The latest 120 live observations were all payload layout v2 with verified raw-capture provenance. They contained 114 private/random addresses and 117 unnamed advertisements, which are real radio observations but not evidence of the same number of physical devices.
- Under live ESP32 ingestion, `/api/devices` returned HTTP 200 in 0.057 seconds, `/api/overview` in 0.030 seconds, and `/api/diagnostics` in 0.006 seconds. No SQLite lock or invalid-payload error was reported.

Limits:

- A BLE advertisement with a randomized address is a real received radio identity, but it cannot be counted as a distinct physical device without stronger continuity evidence.
- This scanner currently performs BLE discovery. A Classic Bluetooth-only headset/TWS can be absent unless it is advertising BLE; Classic inquiry is a separate ESP32 capability and is not silently substituted for BLE results.

## Phase: Journal-Based RSSI Window Location Evidence

Changed:

- Replaced live movement evaluation based on operator RSSI delta and EMA alpha with the RSSI-only time-window method from [Scientific Reports](https://www.nature.com/articles/s41598-022-06201-y): two consecutive five-reading means, absolute RSSI difference, beta-weighted scanner evidence, `tanh` change metric, and RSSI-only reliability.
- Added `rssi_window_metrics()` and persisted the paper inputs and outputs in each proximity model: window means, absolute dB changes, scanner weights, `rssi_metric`, reliability, threshold, and readiness state.
- Used the one-scanner case as the mathematically valid `n=1` vector form with weight `1.0`; future scanners can contribute additional vector elements without changing the observation schema.
- Removed `movement_rssi_delta` and `rssi_smoothing_alpha` from active application configuration, scanner config payloads, ORM configuration fields, and the settings table. Legacy database columns are left physically intact for compatibility but are no longer read.
- Updated the dashboard to show the RSSI change metric, signal reliability, and window readiness instead of implying a calibrated distance.
- Updated signal-processing, requirements, technical-design, operations, testing, and README documentation with the equations, source papers, and limits.

Validation:

- `PYTHONPYCACHEPREFIX=/tmp/bluetooth-scanner-pycache python3 -m pytest -q` passed: 38 tests, including a live ten-observation ingestion test that verifies the stored paper metric.
- `PYTHONPYCACHEPREFIX=/tmp/bluetooth-scanner-pycache python3 -m compileall -q backend run.py serial_bridge.py tests` passed.
- `node --check dashboard/app.js` passed.
- Runtime database remains clean: observations, identities, logical devices, location estimates, events, heartbeats, and processing errors are all zero.
- No backend server or serial bridge was started in this phase, so no process was left running.

Limits:

- The cited method supports relative RSSI change and proximity-evidence reliability; it does not turn one ESP32's RSSI into universal meters or exact indoor coordinates.
- The `beta=0.85`, five-reading window, and `0.6` movement threshold are the cited paper's experimental algorithm values, not universal physical constants. They are internal and auditable, not operator calibration settings.
- A one-scanner result identifies the scanner installation context only. Exact device coordinates require multiple fixed scanners and a separately measured environmental model or fingerprint dataset.

## Phase: Journal Radial Distance And ESP32 Batch Reliability

Changed:

- Added a reproducible ESP32 BLE log-distance baseline from the ELKHA 2025 evaluation: `d = 10 ^ ((A - RSSI) / (10n))`, with the paper's `A=-47 dBm` and `n=2` kept as internal literature constants rather than dashboard calibration settings.
- `estimated_distance_m` now contains the radial model output for real RSSI observations. Each result records its model status and whether it is within the paper's approximately four-metre clear-line-of-sight validation range.
- The location map now renders scanner-centred uncertainty rings using the modeled radius. It does not invent a device bearing or point coordinate from one scanner.
- Increased the ESP32 upload JSON document capacity from 24 KB to 64 KB so 40 active-scan observations with raw ADV/scan-response payloads are not truncated.
- Increased USB bridge/firmware response timeout to 60 seconds and released firmware `esp32-ble-scanner-1.2.1`, preventing slow SQLite processing from causing repeated/incomplete serial frames.
- Added a backend timestamp fallback that reuses verified `scanner_time` when a synchronized observation frame omitted `observed_at`.
- Removed stale RSSI movement/smoothing variables from `.env` and `.env.example`.

Validation:

- `python3 -m platformio run --project-dir firmware -t upload --upload-port /dev/cu.usbserial-0001` passed. ESP32 MAC: `cc:db:a7:94:bc:8c`.
- `PYTHONPYCACHEPREFIX=/tmp/bluetooth-scanner-pycache python3 -m pytest -q` passed: 39 tests.
- `PYTHONPYCACHEPREFIX=/tmp/bluetooth-scanner-pycache python3 -m compileall -q backend run.py serial_bridge.py tests` passed.
- `node --check dashboard/app.js` passed.
- Controlled real ESP32 verification on port `8020`: heartbeat/configuration returned HTTP 200; repeated batches of 40 returned HTTP 200 with payloads around 29 KB; no JSON truncation or batch 422 occurred after firmware `1.2.1` and the 60-second timeout.
- API output contained non-null modeled distances and scanner-centre coordinates, with examples correctly marked `outside_published_baseline_range` when RSSI implied a distance beyond the paper's validated range.
- The temporary server and serial bridge were stopped after verification. No process remains listening on port `8020`.

Limits:

- The distance is a radial model estimate. It is not a measured distance and does not provide direction or an exact indoor coordinate with one scanner.
- The ELKHA paper reports its useful accuracy only in a limited clear-line-of-sight range; weak observations such as `-90 dBm` can produce very large modeled radii and are explicitly marked outside that validation range.
- The existing runtime observations from the verification are real ESP32 data and were not deleted in this phase.

## Phase: USB Large-Frame Integrity And Runtime Shutdown Reliability

Changed:

- Replaced `readline()`-based serial ingestion with a byte-stream line reassembler. Partial reads no longer receive artificial newlines inside a JSON body.
- Added bridge-side JSON validation. A malformed frame is rejected before FastAPI and receives a non-success acknowledgement so the ESP32 retains and retries the original batch instead of inserting a bad payload.
- Diagnosed the remaining truncation from the real frame context: firmware `String body` ended in the middle of `raw_scan_response_payload` when large batches exhausted the heap while making a second serialized copy.
- Changed firmware batch upload to stream `serializeJson()` directly to `Serial`, call `Serial.flush()` before waiting for the acknowledgement, and released firmware `esp32-ble-scanner-1.2.2`.
- Moved periodic SQLite presence/scanner refresh into a cancellable worker thread and handled a busy local database as a skipped refresh cycle, so stopping the runner does not block for a pool timeout.
- Added serial regression tests for split 28 KB JSON bodies and pre-HTTP malformed-frame rejection.
- Added identity-volume diagnostics to distinguish raw observed identities, randomized identities, logical devices, and actual correlation rows in the dashboard.

Validation:

- `PYTHONPYCACHEPREFIX=/tmp/bluetooth-scanner-pycache python3 -m pytest -q` passed: 41 tests.
- `PYTHONPYCACHEPREFIX=/tmp/bluetooth-scanner-pycache python3 -m compileall -q backend run.py serial_bridge.py tests` passed.
- `node --check dashboard/app.js` passed.
- `python3 -m platformio run --project-dir firmware` passed; the firmware was flashed successfully to the connected ESP32 at `/dev/cu.usbserial-0001`, MAC `cc:db:a7:94:bc:8c`.
- Real verification on port `8020` accepted repeated batches of 40 observations with payloads from approximately 28.6 KB to 31.5 KB. Every observed batch returned HTTP 200; no `422` and no malformed-frame log occurred after firmware `1.2.2`.
- `/api/diagnostics` during verification reported `invalid_payload_count=0`, `recent_errors=[]`, and scanner buffer/pending/dropped counts all `0`.
- The test server on port `8020` and its serial bridge were stopped after verification. A separate local runner appeared afterwards and was left untouched because it was not created by this phase.

Identity audit:

- The database contained `2917` raw observations, `249` observed identities, `249` logical devices, and `0` identity correlations. `244` identities were randomized-address observations. Therefore the high device count was not produced by a correlation loop or duplicate logical merge; it is the direct identity view of nearby BLE advertisements accumulated during real scans.
- `Apple, Inc.` is a parsed Bluetooth SIG manufacturer namespace in the raw advertisement, not proof that 187 physical Apple products were present. Randomized addresses remain separate unless an approved evidence path or manual operator decision links them.

## Phase: Local BLE Coverage Filter And Clean Present View

Changed:

- Audited one hour of real ESP32 data before changing collection behavior: `3675` observations produced `398` observed identities and `398` logical devices with `0` identity-correlation rows. The excessive count was not created by the correlation pipeline.
- Confirmed that `393` of those `398` addresses were reported as random addresses and that `304` identities never exceeded `-85 dBm`. Repeated generic Apple Continuity payload layouts were intentionally not treated as proof of one physical device.
- Raised the scanner's default and active operational capture floor from `-95 dBm` to `-85 dBm`. This is a local coverage/noise filter based on the recorded deployment distribution, not a universal distance calibration.
- Changed Live Devices to open on `Present devices`, covering `active`, `newly_detected`, and `returned`; operators can still select `All history` to inspect missing and offline records.
- Kept the Location view backed by the complete device history so an offline device's last scanner location remains visible instead of disappearing with the Live filter.
- Split the overview counts into `Temporarily missing` and `Offline records` so accumulated historical addresses are not presented as currently nearby devices.
- Increased the firmware serial-control line capacity so complete scanner configuration responses can be applied, and released firmware `esp32-ble-scanner-1.2.4` with the `-85 dBm` default.
- Cleared all runtime scan data after the fix: `3811` observations, `413` observed identities, `413` logical devices, `3343` events, `3811` location estimates, and `47` heartbeats. Scanner registration, token, coordinates, monitored location, system settings, and scanner configuration were preserved.

Validation:

- `PYTHONPYCACHEPREFIX=/tmp/bluetooth-scanner-pycache python3 -m pytest -q` passed: 42 tests.
- `PYTHONPYCACHEPREFIX=/tmp/bluetooth-scanner-pycache python3 -m compileall -q backend run.py serial_bridge.py tests` passed.
- `node --check dashboard/app.js` passed.
- Firmware `esp32-ble-scanner-1.2.3` was built, flashed, and verified against the real ESP32. Real batch sizes fell from a fixed maximum of 40 to `6-18`; the checked observations had RSSI from `-85` to `-74 dBm`, confirming that the collection floor is applied by the scanner.
- Firmware `esp32-ble-scanner-1.2.4` built and flashed successfully. Its final runtime/config-sync check is blocked by the connected ESP32 repeatedly reporting `Brownout detector was triggered`; brownout protection was not disabled.
- Final database verification reports zero rows for observations, observed identities, logical devices, events, location estimates, heartbeats, and processing errors. Scanner `scn_dev_lab_001` remains registered at `-6.26085, 106.960005` with desired configuration version `3` and `rssi_min=-85`.
- No backend or serial bridge process was left listening on port `8000`.

Limits:

- The capture floor reduces very weak ambient reception; it cannot reveal the exact number of physical devices behind rotating BLE private addresses.
- Bluetooth private addresses can rotate periodically. Generic name, manufacturer namespace, or payload-layout similarity is insufficient for an automatic physical-device merge, so uncertain identities remain separate and auditable.
- The ESP32 brownout is a power-integrity failure at boot. It must be resolved with a stable USB port/cable or powered supply before the latest firmware can complete an end-to-end runtime check; disabling the detector would hide a hardware fault and risk corrupted operation.

## Phase: macOS USB Disconnect Diagnosis

Investigated:

- Confirmed `run.py`, FastAPI, the dashboard endpoints, and the serial reconnect loop were operating normally while scanner heartbeat data remained absent.
- Confirmed `/dev/cu.usbserial-0001` was owned only by the active `run.py` serial bridge, so the failure was not caused by a second process competing for the port.
- Inspected the macOS USB topology. The CP2102/ESP32 is connected through a nested USB 2.0 hub shared with Infinix and Samsung phones, an MXT USB device, and a USB LAN adapter.
- Read the macOS kernel USB log. It recorded CP2102 endpoint transaction errors followed by `upstream hub is terminating`, removal of both CP2102 and sibling USB devices, and subsequent re-enumeration. This directly explains pyserial's `[Errno 6] Device not configured` and the scanner's offline state.

Changed:

- No application, firmware, database, scanner configuration, or runtime data was changed. The active user-started `run.py` process was left running so its existing reconnect loop can recover automatically after the ESP32 is moved to stable USB power.

Required hardware action:

- Connect the ESP32 through a direct USB-C data adapter or a separately powered USB hub, use a known-good data cable, and remove other high-load devices from the same unpowered hub path.
- Do not disable ESP32 brownout protection; doing so would conceal unstable power without fixing USB detachments at the host level.

## Phase: ESP32 Recovery And Graceful Runner Shutdown

Changed:

- Recovered the real ESP32 after the other devices were removed from the unstable shared USB hub and the board completed a fresh power cycle.
- Added an explicit realtime-broker shutdown signal. Active SSE dashboard streams now receive an internal close sentinel instead of keeping Uvicorn's connection-drain phase open indefinitely.
- Replaced the `uvicorn.run()` helper with a small `uvicorn.Server` subclass in `run.py` so the SSE broker is closed as soon as `SIGINT` is received, before Uvicorn waits for open HTTP connections.
- Handled the re-raised `KeyboardInterrupt` in the same way as Uvicorn's official helper, preserving a clean process exit code.
- Added `tests/test_realtime.py` to prove that a pending SSE subscriber exits immediately when shutdown is requested.

Validation:

- Real ESP32 config fetch and heartbeat returned HTTP 200. The backend reported scanner `scn_dev_lab_001` online with firmware `esp32-ble-scanner-1.2.4` and applied configuration version `3`.
- Real observation batches of `4-10` records returned HTTP 200 with no processing errors. The final retained dataset contains `69` real observations across `26` raw identities with RSSI from `-85` to `-65 dBm`.
- Scanner health reported buffer usage `0`, pending observations `0`, and dropped observations `0`.
- Shutdown was tested with eight active dashboard SSE connections. A single `Ctrl+C` completed application shutdown in under one second with exit code `0`; no runner, serial bridge, port listener, or serial-port owner remained.
- `python3 -m pytest -q` passed: 43 tests.
- `PYTHONPYCACHEPREFIX=/tmp/bluetooth-scanner-pycache python3 -m compileall -q backend run.py serial_bridge.py tests` passed.

Notes:

- The 69 observations from this phase are real post-recovery ESP32 captures and were intentionally retained. No simulator or generated observation was used.
- The server used for verification was stopped. The next normal start remains `python3 run.py` by the operator.

## Phase: ESP32 Device-Name Enrichment Feasibility Review

Investigated:

- Inspected the real raw advertisement and scan-response payloads behind the current device list. The value `*Tqbdf!Usbwfm@` is not a dashboard decoding error: those exact bytes are transmitted in a Complete Local Name (`AD type 0x09`) scan-response field by address `24:11:11:b3:eb:ee`.
- Confirmed that the sampled Apple manufacturer-data frames do not contain a Shortened or Complete Local Name. Active scanning cannot recover a name that the advertiser does not place in ADV or scan-response data.
- Reviewed the Bluetooth SIG GAP/GATT definitions. A connectable BLE peripheral may expose Device Name (`0x2A00`) and optional Device Information Service values such as model (`0x2A24`), firmware (`0x2A26`), hardware (`0x2A27`), manufacturer (`0x2A29`), and PnP ID (`0x2A50`). Reading them requires a GATT connection and remains subject to device permissions and pairing requirements.
- Reviewed the ESP32 Bluetooth stack options. The current NimBLE host is BLE-only; Espressif Bluedroid supports BLE plus Bluetooth Classic on the original ESP32. Classic inquiry/EIR, Remote Name Request, Class of Device, and SDP can provide clearer names/categories for discoverable TWS and other BR/EDR devices.
- Found that the installed NimBLE-Arduino `1.4.3` can report an incorrect `isConnectable()` value for some legacy advertising types. Upstream release `2.3.6` explicitly lists this fix. GATT enrichment must not use the current stored connectable flag until that dependency is upgraded or eligibility is derived directly from the advertising type.

Recommended implementation order:

- First correct connection eligibility and record name provenance: complete/shortened, ADV/scan response, GATT, Classic EIR, or Classic Remote Name.
- Add a scheduled BLE GATT enrichment queue for truly connectable devices and store each directly read characteristic separately from inferred identity data.
- Add alternating Bluetooth Classic inquiry windows through an ESP-IDF/Bluedroid firmware migration for clearer TWS/headset discovery while retaining the existing serial JSON/backend contract.
- Keep manufacturer-only, non-connectable Apple/Find My frames unnamed when no name is transmitted. Protocol/category labels may be shown, but a product or owner name must not be fabricated.

Changed:

- No source code, firmware, database, scanner configuration, or runtime process was changed in this review phase.

## Phase: Direct BLE Identity Enrichment And Durable Presence Semantics

Changed:

- Added the `device_enrichments` storage model, API schema, Alembic migration `0004_device_enrichments`, ingestion path, serializers, and device-detail history. Direct GATT reads are stored separately from advertisement inference with their source observation, scanner, status, raw characteristic hex, duration, and error.
- Released and flashed firmware `esp32-ble-scanner-1.3.1`. The ESP32 now queues truly connectable legacy BLE advertisers and reads standard GAP/Device Information characteristics: Device Name, Appearance, System ID, model, serial, firmware/hardware/software revision, manufacturer, IEEE certification data, and PnP ID. It enumerates services and does not force pairing.
- Corrected legacy connectability classification for NimBLE-Arduino `1.4.3`: only `ADV_IND` and directed advertising are treated as connectable. `ADV_SCAN_IND` and `ADV_NONCONN_IND` are no longer sent to the GATT connector.
- Added name provenance to the API/dashboard and a Direct Device Information section. A GATT Device Name can replace an address-only display label, but failures and protected/absent values remain explicit rather than being guessed.
- Fixed the long-running `{}` batch failure. Firmware no longer requests a fixed 64 KB ArduinoJson block after NimBLE has occupied normal runtime heap; capacity follows the actual frame, one USB frame is capped at 12 observations, overflow is checked before transmission, and the bridge rejects semantically empty batch objects before HTTP.
- Separated unresolved rotating addresses from durable device presence. A public/stable address, operator-confirmed identity, accepted multi-identity correlation, or directly read GATT serial/System ID can retain normal missing/offline state. An uncorrelated random address becomes `identity_expired` instead of claiming that a physical device went offline.
- Excluded unresolved random addresses from `Active devices`, movement, missing, and offline device metrics. The overview now reports `Active unresolved IDs` and `Expired random IDs` separately; expired records are hidden from the default device/map queries but remain inspectable with `include_expired=true` or the dashboard checkbox.
- Added idempotent expired-state maintenance and a distinct `device_identity_reappeared` event. Removed only `268` invalid self-transitions (`identity_expired -> identity_expired`) produced during discovery of the guard bug; no Bluetooth observation, identity, valid presence event, scanner registration, or configuration was deleted.
- Updated `README.md`, `docs/operations.md`, and `docs/api.md` for firmware `1.3.1`, GATT provenance, serial memory behavior, and offline-versus-expired semantics.

Validation:

- `env PYTHONPYCACHEPREFIX=/tmp/bluetooth-scanner-pycache python3 -m pytest -q` passed: 46 tests.
- Python compileall and `node --check dashboard/app.js` passed.
- PlatformIO build passed for the real ESP32: RAM `33.6%`, flash `48.0%`. Upload to `/dev/cu.usbserial-0001` succeeded; hardware MAC `cc:db:a7:94:bc:8c`.
- Long-running real USB tests accepted repeated batches of `3-12` observations with HTTP 200 after firmware `1.3.1`. No empty batch, malformed JSON, HTTP 422, or processing error remained.
- Direct GATT verification read a device as Device Name `Mac`, manufacturer `Apple Inc.`, model `Mac14,2`, with raw values such as `2A00=4d6163`, `2A24=4d616331342c32`, and `2A29=4170706c6520496e632e`. Connection failures are retained as failures, not converted into names.
- Final presence API verification returned five offline records; all had public address type and `presence_trackable=true`. The expired filter returned only random addresses with `identity_basis=unresolved_randomized_address` and `presence_trackable=false`.
- The final database check reported zero processing errors and zero repeated expired self-transition events.
- The verification runner was stopped cleanly. Port `8000` has no listener and `/dev/cu.usbserial-0001` has no process owner.

Limits:

- GATT enrichment cannot name a non-connectable advertisement, a characteristic that requires authorization, or a device that omits standard identity characteristics. Non-connectable Apple/Find My traffic therefore remains an address/protocol observation unless direct bytes provide another factual label.
- A GATT Device Name, manufacturer, and model improve display information but are not automatically treated as a unique cross-address identity. Only stronger evidence such as serial/System ID, accepted correlation, or operator confirmation makes randomized-address presence durable.
- The current firmware remains BLE-only. Bluetooth Classic inquiry/Remote Name/SDP for Classic-only TWS devices requires a separate Bluedroid firmware phase and was not mixed into this BLE stability change.

## Phase: Durable Device Anchors And Map Interaction Cleanup

Changed:

- Added `location_anchor_observed_at` to logical devices and Alembic migration `0005_device_location_anchor`. Existing rows are backfilled from their last recorded observation without deleting scan history.
- Defined a conservative anchor policy: a device starts at the scanner-location snapshot where it was observed; moving or editing the same scanner does not drag that device; a newer observation from a different scanner ID can move the anchor.
- Added auditable anchor provenance to device API responses, observation processing notes, and location-estimate details. Raw per-observation scanner coordinates remain available even when they do not update the current anchor.
- Updated `run.py` to apply pending Alembic migrations automatically before startup, preserving the one-command `python3 run.py` flow for an existing database.
- Changed the Leaflet map to draw uncertainty circles from each device's stored anchor instead of the scanner's current coordinates.
- Added count markers for devices sharing one anchor, a scrollable per-device popup, status-specific online/missing/offline marker colors, and a native device-detail dialog that can open directly from the map.
- Changed map framing to include both current scanner coordinates and retained device anchors, so devices remain visible after the scanner is moved.
- Reworked the dashboard visual system to a compact operational layout: removed gradients, glass effects, decorative backgrounds, hover movement, oversized radii, page-section cards, and external font loading.
- Improved responsive wrapping for proximity text, tables, forms, map controls, detail fields, address data, and mobile navigation.
- Removed repeated page subtitles and shortened map status text while retaining technical provenance in device details.

Validation:

- `python3 -m pytest -q` passed: 47 tests.
- Added a regression test proving that changing a scanner from Tebet coordinates to Bekasi coordinates does not move a device anchor observed by that same scanner.
- Existing multi-scanner regression coverage still proves that a newer observation by a different scanner moves the same logical device and that a delayed old observation cannot rewind it.
- `node --check dashboard/app.js` passed.
- Migration `0004 -> 0005` completed successfully on the active SQLite database; no observation or identity rows were deleted.
- Dashboard assets and live API endpoints returned HTTP 200 during the temporary backend check. Interactive browser verification was stopped at the operator's request and is not claimed as complete.
- The temporary backend ran with the serial bridge disabled, so no test or generated Bluetooth observation entered the database. It was stopped cleanly and port `8020` has no listener.

Limits:

- A single movable scanner cannot factually distinguish scanner movement from Bluetooth-device movement. Therefore the same scanner ID never relocates an existing device anchor automatically.
- A factual cross-location move requires a newer observation from a different fixed scanner identity, or a future explicit operator relocation workflow.
- The stored point is a scanner-location snapshot, not an exact Bluetooth-device coordinate. RSSI still provides only a radial signal model with no bearing from one scanner.

## Phase: Reproducible Setup And Engineering Documentation

Changed:

- Audited every Markdown file present in the workspace at final review, including project rules, phase documents, references, hidden reference issue templates, dependency licenses, and generated PlatformIO library documentation. Generated/vendor documents remain dependencies, not application contracts.
- Replaced `.gitignore` with coverage for all Markdown files, `.env` secrets, generated firmware configuration, SQLite databases and sidecars, runtime locks, virtual environments, Python caches, test/coverage output, PlatformIO output, logs, editors, operating-system metadata, and local inspection artifacts. `.env.example` remains eligible for source control.
- Added `setup_project.py` with idempotent `backend`, `firmware`, `flash`, and `all` targets. It creates `.venv`, installs dependencies, preserves existing environment values, generates missing secrets, writes the firmware scanner ID, applies migrations, bootstraps the local scanner, builds firmware, auto-detects a serial port, and uploads only for `flash` or `all`.
- Added `requirements-firmware.txt` so PlatformIO is installed in the project virtual environment instead of relying on an undocumented global command.
- Changed database configuration to default to project-relative `data/bluetooth_scanner.sqlite3`. `BLUETOOTH_SCANNER_DATA_DIR` controls local storage and `DATABASE_URL` remains the explicit PostgreSQL/custom override.
- Added automatic SQLite parent-directory creation and retained WAL, foreign-key, busy-timeout, and single-connection local behavior.
- Preserved the existing root SQLite installation by normalizing the legacy relative URL to `BLUETOOTH_SCANNER_DATA_DIR=.`. No database file was moved, replaced, or deleted.
- Updated `run.py` to re-execute through the project virtual environment when available and to report the setup command when a dependency is missing.
- Changed Alembic configuration to resolve migrations relative to `backend/alembic.ini`, so documented commands work from the project root instead of depending on the shell working directory.
- Removed unused Wi-Fi/backend/token placeholders from `firmware/include/config.example.h`; the current USB firmware configuration now contains only values it actually consumes.
- Rewrote `README.md` with first-install, setup-target, SQLite/PostgreSQL, firmware, run/stop, location, identity, development, production, and documentation instructions.
- Added `docs/engineering-guide.md` as the technical source of truth for scope, limitations, runtime topology, module and function ownership, backend lifecycle, serial protocol, firmware queue/GATT behavior, ingestion, time trust, raw/processed separation, correlation, presence, location anchors, RSSI/movement evidence, data model, concurrency, security, extension procedures, tests, invariants, and known gaps.
- Rewrote `docs/api.md`, `docs/operations.md`, `docs/testing.md`, `docs/project-structure.md`, and `docs/privacy.md` with implementation-accurate contracts and maintenance procedures.
- Reconciled older phase, calibration, correlation, and reference-comparison documents with the current durable-anchor policy, USB-only transport, bounded volatile firmware queue, active GATT enrichment, scanner-token placement, and absence of an automatic retention worker.
- Added setup regression coverage for default/relative SQLite paths, legacy database preservation, firmware scanner configuration, and Alembic path independence.

Validation:

- `python3 setup_project.py backend` created the project virtual environment, installed backend dependencies, applied migrations, and retained scanner `scn_dev_lab_001` without creating Bluetooth observations.
- `python3 setup_project.py backend --skip-dependencies` completed successfully against the prepared environment.
- A fresh temporary SQLite database migrated successfully through revisions `0001` to `0005`; the configured operational database reports `0005 (head)`.
- `python3 setup_project.py firmware --skip-dependencies` built the ESP32 firmware successfully with PlatformIO 6.1.19. Memory use is RAM 33.6% (`110060/327680`) and flash 48.0% (`629421/1310720`).
- `.venv/bin/python -m pytest -q` passed: 52 tests.
- Python `compileall` passed for backend, runner, bridge, setup, and tests with bytecode cache directed to `/tmp`.
- `node --check dashboard/app.js` passed.
- Git ignore behavior was verified in an isolated temporary repository: root and nested Markdown, `.env`, firmware config, SQLite, and PlatformIO build output are ignored, while `.env.example` is not ignored.
- Source audit found no simulator, dummy-data, fake-data, or generated-observation path in the runtime, setup, backend, firmware, dashboard, or environment template.
- Port 8000 had no listener after validation. The backend server was not started and no runtime process was left active.

Limits:

- Firmware upload was not executed because no ESP32 serial port was connected during final validation. The build and automatic `flash` target are ready; upload requires the physical board and an unused serial port.
- All Markdown files are intentionally ignored at the user's request. Existing tracked Markdown in a future or external Git repository would require a separate index removal because `.gitignore` does not untrack files.
- Retention settings remain configuration only until a bounded archival/cleanup worker is implemented and tested.

## Phase: Backend Documentation And Test Console Scope

Changed:

- Updated `.gitignore` so root `README.md`, root `CHANGELOG.md`, and maintained backend documentation under `docs/` are repository-eligible.
- Explicitly kept `docs/phase-1-requirement-analysis.md` and `docs/phase-2-technical-design.md` ignored. Markdown under references, virtual environments, PlatformIO dependencies, and other vendor paths remains ignored.
- Anchored the README and changelog exceptions to the repository root so a nested vendor `README.md` is not accidentally included.
- Reframed `README.md` around the FastAPI backend, scanner transport, persistence, processing, API, deployment, and operational contracts.
- Reframed the engineering, API, operations, testing, structure, privacy, and reference-comparison documents around backend ownership. The files under `dashboard/` are now consistently documented as a non-production test console.
- Removed the Settings navigation item, Settings view, settings state, API loading, dynamic form generation, save handler, event subscription, and form binding from the test console.
- Removed all calibration, RSSI/correlation tuning, retention, and internal policy fields from the UI. Backend processing and correlation policy remain server-owned and unchanged.
- Preserved scanner management, device inspection, map evidence, events, and diagnostics as development-only backend inspection surfaces.

Validation:

- Verified ignore behavior in an isolated Git repository: `README.md`, `CHANGELOG.md`, `docs/engineering-guide.md`, and `docs/api.md` are not ignored.
- Verified `docs/phase-1-requirement-analysis.md`, `docs/phase-2-technical-design.md`, and `references/MetaRadar/README.md` remain ignored.
- Confirmed the project directory itself is not currently a Git repository, so no staging or commit operation was claimed.
- Confirmed no Settings view, `settingsForm`, settings API call, settings renderer, calibration field, or correlation-alpha field remains in `dashboard/index.html` or `dashboard/app.js`.
- `.venv/bin/python -m pytest -q` passed: 52 tests.
- Python `compileall` passed for backend, runner, bridge, setup, and tests.
- `node --check dashboard/app.js` passed.
- Port 8000 had no listener. No backend or serial process was started for this phase.

Limits:

- The backend still provides `/api/settings` as an engineering API for existing internal policy records. It is no longer exposed by the test console.
- Repository eligibility is configured, but files cannot be staged or committed until this directory is initialized as a Git repository or connected to its intended repository metadata.

## Phase: Reference Material Exclusion

Changed:

- Excluded the complete `references/` directory because it contains local research papers and third-party comparison projects rather than application source.
- Removed all previously tracked reference entries while preserving the local files for engineering comparison.

Validation:

- Confirmed `git ls-files references` returns no tracked paths.
- Confirmed Git ignores files below `references/` and all 516 local reference files remain available.

## Phase: BLE Signal Finder Backend Contract

Changed:

- Added migration `0006_device_tracking_sessions` and normalized tracking-session, scanner-assignment, focus-sample, and scanner-position records.
- Added REST and dedicated SSE contracts for starting, leasing, inspecting, stopping, and streaming a selected BLE device tracking session.
- Required every session to originate from a stored Bluetooth `Observation`; scanner heartbeat data cannot create a target or produce a focus sample.
- Kept high-rate focus samples outside the normal observation processor so they cannot create logical devices, change presence or movement state, run correlation, or move a location anchor.
- Added accepted-identity target resolution, one-session-per-scanner enforcement, bounded leases, timestamp/sequence checks, EMA signal values, delayed-sample suppression, session summaries, and retention cleanup.
- Added dynamic `tracking_focus` scanner configuration and scanner-authenticated focus batch ingestion.
- Added serial-bridge validation for focus batches and tracking counts to diagnostics.
- Extended explicit scan-data cleanup to remove tracking records in foreign-key-safe order.

Validation:

- A fresh temporary SQLite database migrated successfully from an empty schema through revision `0006`.
- Focused backend, realtime, serial bridge, and existing processing tests passed: 49 tests.
- Regression coverage proves a session requires a real BLE observation, an unrelated address is rejected, duplicate samples are idempotent, Walk positions do not patch scanner installation coordinates, and focus samples do not alter device presence or location state.

Limits:

- Normal ESP32 scan cadence remains unchanged in this phase. Responsive focus telemetry is implemented in the following firmware phase.
- The test console does not consume the new tracking APIs until the dashboard phase is complete.

## Phase: ESP32 Focused Signal Acquisition

Changed:

- Identified the connected board as an ESP32-D0WD-V3 revision 3.0 and updated the reported hardware and firmware versions accordingly.
- Added dynamic `tracking_focus` configuration parsing with bounded accepted identities, sample cadence, and upload cadence.
- Added duplicate-enabled continuous active scanning while a tracking session is armed, without weakening the normal observation RSSI floor.
- Added a dedicated 64-sample focus ring buffer, 200 ms per-target throttling, stable sample and batch identifiers, dropped-sample accounting, and retry-safe in-flight batches.
- Kept normal discovery running during focus mode with software cycle deduplication so focused tracking cannot inflate logical-device records.
- Suspended GATT enrichment during focused tracking to avoid scan interruptions, while retaining the existing normal-mode enrichment flow.
- Added focus-session health fields to scanner heartbeats and a scanner-authenticated focus-batch upload path through the USB serial bridge.
- Reduced normal configuration refresh to five seconds and focused-session refresh to two seconds so start and stop commands reach the cable-connected scanner promptly.

Validation:

- PlatformIO compiled the release firmware successfully for `esp32dev`.
- Final firmware usage is 115,020 bytes of RAM (35.1%) and 637,741 bytes of the application flash partition (48.7%).
- Rebuilt after replacing the race-prone ring-buffer ACK path with a separate immutable in-flight batch.

Limits:

- This phase validates the firmware binary, not RF performance in a controlled measurement environment.
- Focus samples report measured RSSI at the scanner. The ESP32-D0WD-V3 has no GPS or Bluetooth direction-finding hardware and therefore cannot directly report a target bearing or exact coordinate.

## Phase: Signal Finder Test Console And Runtime Concurrency

Changed:

- Made device rows, map markers, anchored-device lists, and the map device selector open the same device-detail drawer.
- Replaced the modal detail view with a desktop side drawer and mobile bottom sheet so the selected map location remains visible while a device is inspected.
- Added Fixed and Walk tracking controls, a persistent Signal Finder panel, live RSSI meter, measured-sample chart, stale-signal state, optional proximity tone, lease renewal, dedicated tracking SSE, and explicit Stop control.
- Fixed mode displays the immutable scanner-location snapshot and a radial model overlay. Walk mode records operator-device GPS samples, displays the measured path and strongest measured point, and never writes those positions into scanner installation coordinates or the device's authoritative location anchor.
- Added selected-device emphasis, count-aware map interaction, status-specific markers, measurement-anchor crosshairs, responsive map controls, and overflow-safe desktop and mobile layouts.
- Added request coalescing and event debounce so repeated scanner events cannot create overlapping refresh queues in one dashboard tab.
- Added migration `0007_observation_query_indexes` for observation receive-time and logical-device/observed-identity queries. Cold overview latency on the operational database dropped from approximately 0.50 seconds to 0.04 seconds and subsequent reads to approximately 0.01 seconds.
- Replaced the single SQLite connection bottleneck with a bounded four-connection pool. WAL, foreign keys, a 30-second busy timeout, zero overflow, and the single-runner process lock remain active.
- Moved every synchronous SQLAlchemy mutation endpoint out of the asyncio event loop. SSE publication now runs as a FastAPI background task after the database response, preventing a waiting mutation from blocking readers that need to finish and release their connections.
- Made repeated tracking starts for the same logical device idempotently renew and return the existing scanner session. A different target still receives a conflict while that scanner is assigned.

Validation:

- Focused backend, tracking, realtime, processing, and serial-bridge tests passed after the concurrency changes: 16 tests.
- Migration `0006 -> 0007` completed on the operational SQLite database without deleting or rewriting Bluetooth observations.
- A load test against a copied real database completed 160 concurrent overview/device/scanner/diagnostic reads, an active tracking SSE stream, a repeated start, and a lease renewal with zero failures. Read p95 was 0.164 seconds and the maximum was 0.254 seconds.
- The same load test stopped the tracking session successfully; no pool timeout, `database is locked`, or HTTP 500 occurred.
- Headless browser validation proved that selecting a real stored BLE device, starting Signal Finder, reloading the page, starting again, and stopping used one session ID and ended in `stopped`.
- Desktop and mobile layout checks found no horizontal overflow, clipped drawer content, or JavaScript exception. The map initialized with its local Leaflet assets and rendered the selected tracking anchor.
- All backend and browser tests used a copy of the real database. No generated Bluetooth observation or simulated scanner payload was submitted.
- The temporary backend and browser were stopped after validation.

Limits:

- The signal meter and tone represent measured RSSI strength and trend, not direction. Moving toward a stronger measurement is an operator-guided search method; it does not make the ESP32 a bearing sensor.
- A Fixed session can only place the measurement at the scanner's configured location. A Walk session can map where measurements were taken, but the strongest point is still not proof of the Bluetooth device's exact coordinate.
- Focus tracking accepts only identities already associated with the selected logical device. It does not merge a new randomized address based on RSSI, name, or proximity alone.

## Phase: Focused Tracking Engineering Documentation

Changed:

- Updated `README.md` for the detected ESP32-D0WD-V3, firmware `1.4.0`, focused scan transport, Signal Finder operation, Fixed/Walk semantics, lease behavior, and the tracking test command.
- Documented every focused tracking REST and SSE endpoint, request field, response state, authentication boundary, idempotency rule, conflict, sample validation rule, and coordinate side effect in `docs/api.md`.
- Added `tracking.py`, its public service responsibilities, topic realtime broker, firmware focus queue, tracking processing flow, display mathematics, normalized tables, concurrency design, retention behavior, invariants, extension constraints, and known hardware gap to `docs/engineering-guide.md`.
- Added firmware requirements, operator procedure, scanner/browser co-location requirement, focus diagnostics, bounded cleanup, and troubleshooting cases to `docs/operations.md`.
- Added focused-session unit coverage, migration expectations, real-hardware acceptance, reload/resume behavior, and test-console smoke criteria to `docs/testing.md`.
- Updated `docs/project-structure.md` with tracking module/test ownership and the device-drawer/Signal Finder test surface.
- Updated `docs/privacy.md` to classify focused RSSI and Walk GPS paths as sensitive data and distinguish focus-history cleanup from the absent normal-observation cleanup worker.
- Added the relative Signal Finder scale and its non-distance meaning to `docs/calibration.md`.
- Added an implementation-specific comparison with the local `df-bluetooth` reference. The scan-select-focus, EMA, stale handling, and audio ideas were adapted to the installed ESP32 and backend leases; nRF52840 firmware, address-only identity, strongest-device auto-selection, and direction claims were not adopted.

Validation:

- Searched maintained documentation for stale firmware `1.3.1`, single-connection SQLite descriptions, and obsolete blanket retention statements; none remain.
- `git diff --check` completed without whitespace errors.
- The documentation consistently identifies `dashboard/` as a non-production backend test console and keeps backend state and API contracts authoritative.

Limits:

- Documentation describes implemented firmware and backend behavior only. It does not claim that browser GPS comes from the ESP32 or that Signal Finder measures target direction.

## Phase: Real-Hardware Tracking And GATT Reliability

Changed:

- Upgraded the ESP32 firmware to `esp32-ble-scanner-1.4.1` and pinned `NimBLE-Arduino` to `2.5.0`, including the required scanner API migration.
- Moved GATT enrichment into one isolated FreeRTOS worker so a slow connection or characteristic read does not block scanner configuration, heartbeat, serial transport, or focused tracking.
- Added a 15-second GATT pipeline deadline, explicit `operation_timeout` and `cancelled` results, focus-triggered cancellation, worker health telemetry, and a 512-byte limit per characteristic value.
- Replaced high-level GATT value reads with direct NimBLE read operations. The scanner does not request pairing automatically; protected characteristics are reported as `security_required`.
- Added scanner hardware provenance to the heartbeat contract and persisted `esp32-d0wd-v3` alongside the firmware version.
- Increased focused-sample freshness from two to six seconds. The value is based on measured 3.1-4.5 second capture-to-ingest latency over the shared CP2102 serial transport and does not alter captured RSSI.
- Made serial-frame decoding strict UTF-8. A corrupted frame is rejected before backend forwarding and acknowledged as retryable, while invalid advertisement-name text is omitted and its raw AD payload remains preserved.
- Acknowledged terminal in-flight tracking batches without storing them, allowing firmware retries to settle after a session is stopped or expires.
- Updated the backend API, operations, engineering, calibration, testing, project-structure, and README documentation for the firmware, worker lifecycle, GATT result states, serial corruption handling, and measured freshness rule.

Validation:

- All 69 backend, tracking, processing, realtime, and serial-bridge tests passed.
- Python bytecode compilation, dashboard JavaScript syntax validation, and `git diff --check` passed.
- PlatformIO built the final release with 115,268 bytes of RAM (35.2%) and 648,001 bytes of the application flash partition (49.4%).
- Flashed and hash-verified the firmware on the connected ESP32-D0WD-V3 revision 3.0 at `/dev/cu.usbserial-0001`; the board MAC reported by the flashing tool was `cc:db:a7:94:bc:8c`.
- The final runtime received scanner configuration, heartbeats, and normal observation batches with HTTP 200. Diagnostics reported a healthy database, zero invalid payloads, and no recent processing errors.
- Real GATT enrichment read the Galaxy A06 Device Name and Appearance characteristics without forced pairing. The scanner continued heartbeats and normal ingestion around the enrichment operation.
- A real Fixed Signal Finder session captured 31 ordered Galaxy A06 samples between -83 and -80 dBm. Every inspected sample was live under the six-second freshness rule and the scanner reported zero dropped focus samples.
- A second real session was stopped explicitly after 13 samples between -91 and -82 dBm. Every sample was `delayed=false`; an in-flight post-stop batch received HTTP 200 without increasing the stored sample count.
- Normal observation batches resumed after focus stopped. The selected device retained its original scanner snapshot at `-6.26085, 106.960005`; focused samples did not mutate the durable location anchor.
- No generated Bluetooth observations, simulated scanners, dummy devices, or placeholder measurements were used.
- The backend and serial bridge were stopped after acceptance. Port 8000 has no listener and no process owns the CP2102 serial port.

Limits:

- The installed ESP32-D0WD-V3 has no GPS and no Bluetooth direction-finding/AoA hardware. One scanner measures signal at its configured anchor; it cannot produce a factual target bearing or exact indoor coordinate.
- GATT enrichment depends on a device being connectable and exposing readable characteristics. It cannot bypass encryption, pairing, authorization, or a device that stops advertising.
- The six-second freshness bound is a measured limit for the current USB serial path. A future network transport or different hardware should be measured independently before changing it.

## Phase: Browser Geolocation Error Handling

Changed:

- Replaced the empty `GPS error:` alert path with explicit secure-context, permission-denied, position-unavailable, timeout, and unknown-error messages.
- Added a Promise-based browser position request so backend save failures are handled instead of becoming unhandled callback rejections.
- Added one network-assisted retry after a high-accuracy timeout or unavailable result. Permission denial stops immediately and is never bypassed.
- Disabled both scanner-location controls while a request is active to prevent overlapping browser prompts and duplicate scanner patches.
- Applied the same error descriptions to Walk position capture.
- Documented macOS Location Services, Wi-Fi positioning, local HTTP, remote HTTPS, and the absence of GPS hardware on the ESP32.
- Corrected the remaining Signal Finder firmware requirement from `1.4.0` to `1.4.1`.

Validation:

- Isolated JavaScript checks confirmed high-accuracy fallback, permission-denied termination, and insecure-context rejection.
- Dashboard JavaScript syntax validation passed.
- All 69 backend and integration tests passed.
- `git diff --check` completed without whitespace errors.
- No backend, serial bridge, simulator, or test-data generator was started.

Limits:

- Browser fallback can only return a position supplied by the operating system. It does not make the Mac or ESP32 a GPS receiver and does not create coordinates when Location Services is unavailable.
- Plain HTTP geolocation remains unavailable from non-local origins by browser policy; deployment through a LAN address requires HTTPS.

## Phase: Continuous Scanner Position And Observation Anchors

Changed:

- Replaced the regressed one-shot browser request with the persistent high-accuracy `watchPosition` flow that starts automatically as soon as the dashboard script loads.
- Targeted automatic browser positions to `LOCAL_SCANNER_ID`, independent of the scanner currently selected in the test console.
- Added `POST /api/scanners/{scanner_id}/position` for timestamped browser coordinates and reported accuracy without incrementing firmware configuration.
- Added scanner position source, observation timestamp, and accuracy columns through migration `0008`.
- Rejected future position timestamps, ignored stale/equal position updates, and emitted realtime refresh events only for applied positions.
- Preserved the last reported browser coordinate when a fix is no longer fresh while retaining explicit freshness, timestamp, and accuracy provenance.
- Changed the durable device-anchor policy so scanner-position updates and heartbeats alone never move a device, while every newer accepted BLE observation can snapshot the latest position of the same or a different scanner.
- Kept missing and offline devices at their last accepted BLE-observation anchor and retained delayed-observation no-rewind protection.
- Added automatic-geolocation wiring, monotonic scanner-position storage, same-scanner movement, stale-fix preservation, and location-event regression coverage.
- Updated the README and maintained backend documentation to define the live-position API and the scanner-update versus BLE-observation boundary.

Validation:

- All 73 backend and integration tests passed.
- Dashboard JavaScript syntax, Python bytecode compilation, and `git diff --check` passed.
- A fresh temporary SQLite database migrated from the empty schema through revision `0008`; the three scanner-position provenance columns were inspected directly.
- The local operational database migrated from `0007` to `0008` without creating or deleting Bluetooth observations.
- No backend, serial bridge, simulator, or generated Bluetooth-data process was started for this phase.
- The pre-existing backend and orphaned serial bridge were stopped; port 8000, the runner lock, and `/dev/cu.usbserial-0001` were free at completion.

Limits:

- Browser location describes the Mac or other browser device. It is valid scanner-position evidence only while that device remains physically co-located with the cable-connected ESP32.
- A persistent watcher cannot force macOS Location Services to produce a fix. Permission denial, disabled Location Services, unavailable Wi-Fi positioning, or a non-secure remote origin still prevent updates.
- A device marker is the scanner position captured when that BLE advertisement was observed. One ESP32 still cannot derive the Bluetooth transmitter's exact coordinate or direction from RSSI.

## Phase: Trusted Local HTTPS And Browser Location Diagnostics

Changed:

- Added an idempotent `python3 setup_project.py https` setup target that generates a private local CA and a certificate covering `localhost`, `127.0.0.1`, and `::1`, then trusts the CA in the current macOS login keychain.
- Kept generated keys and certificates under ignored `.local/` storage and added only their configurable paths to `.env.example`.
- Added HTTPS startup support to `run.py` and CA-verified HTTPS forwarding to the USB serial bridge without disabling certificate validation.
- Added persistent browser-location status for secure context, permission state, watcher state, fix receipt, backend save state, fix age, and reported accuracy.
- Added a coordinate-free browser diagnostic endpoint and corresponding Diagnostics view fields so Safari geolocation failures can be located without exposing the reported position.
- Removed the browser API timeout from the persistent watcher. A separate UI diagnostic timer now reports delayed fixes without terminating continuous location monitoring.

Validation:

- Generated and verified the local certificate chain and confirmed the HTTPS dashboard endpoint returned HTTP 200.
- Confirmed ESP32 configuration, heartbeat, and real observation batches crossed the CA-verified HTTPS serial bridge with HTTP 200.
- Runtime diagnostics first identified Safari at `permission=prompt`, then recorded `fix_received`, `saving`, and `live` after Wi-Fi positioning became available.
- The scanner position endpoint accepted repeated browser fixes with HTTP 200.
- No generated Bluetooth observations, simulated scanner, or placeholder position was used.

Limits:

- HTTP and HTTPS are different browser origins and maintain separate Safari location permissions.
- A Mac location fix normally depends on macOS Location Services and nearby Wi-Fi positioning; HTTPS does not create GPS hardware.
- Browser diagnostics are process-local troubleshooting state and intentionally do not persist coordinates.

## Phase: Stable Operator-Controlled Map Viewport

Changed:

- Separated live marker rendering from Leaflet camera movement so observation, heartbeat, scanner-position, SSE, and interval refreshes preserve the operator's current center and zoom.
- Limited automatic framing to initial available map data, an explicit scanner selection, the new `Fit` command, or an explicit Signal Finder focus.
- Limited scanner-selection framing to the selected scanner and its anchored devices instead of allowing other registered scanner markers to widen the viewport.
- Made pointer, wheel, touch, keyboard, and drag interaction cancel pending automatic framing.
- Disabled panning during Leaflet size invalidation so layout refreshes do not shift the observed area.
- Added dashboard regression tests for refresh-safe viewport behavior and explicit fit controls.

Validation:

- All 81 automated tests passed.
- Dashboard JavaScript syntax validation and Python bytecode compilation passed.
- `git diff --check` passed for the changed dashboard and test files.
- The existing user-started runner was not restarted or stopped during this phase.

Limits:

- Leaflet may still pan enough to keep an opened popup visible; it will not zoom out as part of live data refresh.
- Visual browser automation was not run because Playwright is not installed in the project environment. The camera transition logic is covered by source-level regression tests.

## Phase: Bounded USB Requests And Accurate Live Counts

Changed:

- Reduced the host serial-bridge request timeout from 60 seconds to 8 seconds and the ESP32 response wait from 60 seconds to 12 seconds, both below the 45-second device-missing threshold.
- Released firmware `1.4.2` with the bounded response wait so a missing host acknowledgement cannot pause scanning, heartbeat delivery, configuration polling, and Signal Finder activation for a full minute.
- Updated setup to migrate the exact legacy 60-second bridge timeout while preserving operator-defined values, and to refresh release constants in generated firmware configuration without replacing the scanner identity.
- Added `present_ble_records` to the overview response and separated dashboard counters for all currently observed BLE records, trackable active devices, and unresolved randomized identities.
- Added a dashboard compatibility fallback that derives present BLE records from trackable and unresolved counts while an already-running older backend has not yet loaded the new response field.
- Normalized invalid browser geolocation epochs to the callback receipt time while preserving the browser-provided coordinates and accuracy.
- Added regression coverage for timeout ordering, setup migration, firmware configuration refresh, overview count semantics, and browser timestamp normalization.

Validation:

- Runtime history showed a 66-second observation and heartbeat gap while a Signal Finder session remained `arming`; the session became `live` immediately after scanner communication resumed.
- The recovered scanner delivered real observation batches, tracking samples, and heartbeats without processing errors or dropped buffered observations.
- All 87 automated tests passed.
- Dashboard JavaScript syntax validation, Python bytecode compilation, and `git diff --check` passed.
- PlatformIO built firmware `1.4.2` successfully at 35.2% RAM and 49.4% flash usage.
- No simulated scanner, generated Bluetooth observation, placeholder coordinate, or fake device record was introduced.
- The user-started runner and attached ESP32 were not stopped, restarted, or flashed during this phase.

Limits:

- The exact request whose acknowledgement was lost is not persisted in the database; the observed 66-second transport stall identifies the failure class but not that individual request.
- The running ESP32 continues to use its previously flashed firmware until firmware `1.4.2` is flashed and the runner is restarted.
- `present_ble_records` counts current logical BLE records, not guaranteed unique physical devices; unresolved rotating identities remain explicitly separated.

## Phase: Continuous RF Capture, Independent Transport, And Apple Evidence

Changed:

- Removed the `-85 dBm` radio-admission cliff. Firmware now retains valid weak BLE observations down to the practical ESP32 capture floor of `-110 dBm`, and migration `0009` updates existing scanner configurations to the same value.
- Separated factual radio ingestion from dashboard admission. Anonymous randomized manufacturer-only broadcasts are classified as `transient_broadcast` and hidden by default, directly named randomized broadcasts remain inspectable as `named_broadcast_candidate`, and stable or operator-confirmed records remain `device_candidate`.
- Added the explicit `include_transient` device-query control and separate visible-candidate overview count so operators can inspect raw rotating traffic without presenting it as a list of unique physical devices.
- Kept unresolved named randomized identities out of durable presence tracking. They remain observable evidence but expire instead of accumulating as long-lived offline-device records.
- Made focused tracking samples refresh the selected identity and logical-device presence without creating duplicate raw observations, changing identity correlation, or moving the device's last BLE-observation anchor.
- Released firmware `1.5.0` with a dedicated FreeRTOS transport task. Configuration polling, heartbeats, serial or HTTP acknowledgements, retries, and normal or focused uploads no longer execute in the BLE scan loop.
- Added mutex-protected observation, tracking, configuration, timestamp, and identifier state shared by the scan and transport tasks.
- Added retry-stable pending batches, including partial JSON-capacity handling, so acknowledged records are removed exactly once while unsent records remain queued with the same batch identity.
- Added real pending, dropped, buffer-usage, request-duration, request-status, timeout, and transport-failure telemetry to scanner heartbeats.
- Added bounded Apple Continuity TLV parsing for Nearby Info, Handoff, Tethering Target, Magic Switch, Nearby Action, Proximity Pairing, and AirPlay advertisements while preserving raw manufacturer bytes in the original observation.
- Added proposal-only Apple transition correlation using subtype continuity, protocol tokens, Handoff sequence evidence, transition timing, RSSI continuity, and exact GATT model evidence. These proposals never merge identities automatically and explicitly state that they are not confirmed physical-device identities.
- Added dashboard visibility controls and Apple evidence summaries, and updated the maintained backend, API, operations, privacy, calibration, testing, and correlation documentation.

Validation:

- All 93 backend, firmware-contract, dashboard, tracking, correlation, and integration tests passed.
- Dashboard JavaScript syntax validation passed.
- A fresh SQLite database migrated from the empty schema through revision `0009`.
- PlatformIO built firmware `1.5.0` successfully at 36.8% RAM and 49.7% flash usage.
- `git diff --check` completed without whitespace errors.
- No backend or serial-bridge process was started or stopped, and the attached ESP32 was not flashed.

Limits:

- A valid BLE packet is factual radio evidence, but it does not prove that each observed address belongs to a different physical device. Address rotation, relaying, spoofing, and repeated Apple Continuity broadcasts cannot be resolved from RSSI alone.
- The default dashboard filter reduces rotating-address noise; it does not delete weak raw observations or claim that hidden broadcasts are fake.
- Apple transition evidence can support an operator decision, but it cannot establish a permanent physical identity when the protocol exposes no stable identifier.
- Firmware `1.5.0` behavior begins only after an explicit device flash. The next normal `run.py` startup applies database revision `0009`; neither operation was forced during this phase.
