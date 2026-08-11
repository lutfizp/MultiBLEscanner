# Bluetooth Scanner

Bluetooth Scanner is a backend service for Bluetooth Low Energy observations collected by one or more ESP32 scanners. The current transport is an ESP32 connected to a host computer over USB serial. The host forwards scanner heartbeats, configuration requests, and observation batches to FastAPI, which owns validation, persistence, correlation, presence state, location anchors, events, and diagnostics.

`dashboard/` is a bundled test console for inspecting backend responses during development and hardware verification. It is not a production frontend or part of the backend contract.

The implementation preserves the distinction between measured data and inference:

- raw advertising and scan-response bytes are retained;
- parsed AD structures record their source and parse status;
- GATT values are stored separately as direct enrichment reads;
- official Flipper Zero serial-profile UUIDs are classified from verified raw advertisements and exposed with their supporting evidence;
- randomized addresses remain separate unless an approved evidence path or operator decision links them;
- RSSI produces radial signal evidence, not a bearing or exact indoor coordinate;
- a device location point is the scanner-location snapshot where the device was observed, not the physical Bluetooth transmitter coordinate.

## Requirements

- Python 3.9 or newer on macOS or Linux. The local runner currently uses a POSIX process lock.
- The detected ESP32-D0WD-V3 development board, supported through PlatformIO's `esp32dev` target.
- A USB data cable and stable USB power.
- Internet access during dependency and PlatformIO package installation.
- Optional browser internet access when using the test console's map and reverse-geocoding helpers.

The backend does not require Node.js. PlatformIO is installed into the project virtual environment by the setup script when firmware work is requested.

## First Installation

With the ESP32 connected, prepare the backend, create the SQLite database, build the firmware, and flash the detected board:

```bash
python3 setup_project.py all
```

Then start the system:

```bash
python3 run.py
```

The backend listens on the URL printed by the runner. Its optional test console is normally available at:

```text
http://127.0.0.1:8000/dashboard/
```

`run.py` automatically uses `.venv` when it exists. It applies pending Alembic migrations, ensures the configured local scanner record exists, starts FastAPI, and starts the USB serial bridge. It does not generate Bluetooth observations, start a simulator, or require the test console to be open.

## Setup Targets

The setup script is idempotent and does not overwrite existing secrets.

| Command | Effect |
| --- | --- |
| `python3 setup_project.py backend` | Creates `.venv`, installs backend dependencies, creates `.env`, initializes SQLite, applies migrations, and ensures the local scanner record. |
| `python3 setup_project.py firmware` | Installs PlatformIO, generates `firmware/include/config.h`, and builds firmware without uploading it. |
| `python3 setup_project.py flash` | Builds and uploads firmware to the auto-detected ESP32. |
| `python3 setup_project.py flash --port /dev/cu.usbserial-0001` | Uploads through an explicit serial port. |
| `python3 setup_project.py all` | Runs backend setup and flashes firmware. |
| `python3 setup_project.py backend --skip-dependencies` | Reuses an existing `.venv` and only performs environment/database initialization. |

The `all` and `flash` targets intentionally modify the connected ESP32. Stop `run.py`, PlatformIO Monitor, Arduino Serial Monitor, and other serial clients before flashing.

## Database Location

Fresh local installations use:

```text
data/bluetooth_scanner.sqlite3
```

The path is derived from the project root and `BLUETOOTH_SCANNER_DATA_DIR`; it does not contain a user-specific absolute path. Relative values are resolved from the project root, not from the shell's current working directory.

```dotenv
BLUETOOTH_SCANNER_DATA_DIR=data
```

An absolute data directory is also supported:

```dotenv
BLUETOOTH_SCANNER_DATA_DIR=/srv/bluetooth-scanner/data
```

`DATABASE_URL` overrides the local SQLite selection for PostgreSQL or a custom database deployment:

```dotenv
DATABASE_URL=postgresql+psycopg://bluetooth:password@database.example/bluetooth_scanner
```

The setup script recognizes the previous `sqlite:///bluetooth_scanner.sqlite3` configuration and converts it to `BLUETOOTH_SCANNER_DATA_DIR=.` without moving or replacing the existing database.

## Firmware And USB Transport

The firmware performs active BLE scans and emits framed request messages over USB serial. The serial bridge:

- auto-detects CP210x, CH340, USB UART, and common ESP32 serial ports;
- supplies synchronized host UTC time;
- adds the scanner bearer token before forwarding requests;
- rejects malformed and empty JSON before HTTP;
- returns backend status and configuration responses to the firmware;
- reconnects after temporary USB detachments.

Firmware release `esp32-ble-scanner-1.7.0` targets the detected ESP32-D0WD-V3 board and pins NimBLE-Arduino 2.5.0. It runs continuous asynchronous active scanning, captures separate ADV and scan-response payloads down to the practical `-110 dBm` receiver floor, admits at most one normal record per address in each scan window, uses a bounded 96-observation RAM queue, keeps IDs stable across retries and unique across scanner boots, and uploads at most 12 normal observations per serial frame. Configuration, heartbeat, serial framing, HTTP acknowledgement waits, and upload retries run in a dedicated 32 KB FreeRTOS transport task with deadline-aware scheduling. Observation batches, heartbeats, and GATT reports use fixed stack-backed JSON documents and serialize directly to USB instead of allocating a second complete body in heap. Radio observations and GATT worker output use separate firmware structures; GATT results are never embedded in an observation batch. When queued observations exceed one frame, acknowledged frames drain consecutively until the backlog is bounded; a local content-capacity failure retries a smaller unsent slice, while an immutable frame that reached transport observes the configured retry interval. Firmware and bridge use `230400` baud; 12-item frames and the observation-idempotency database index keep transport above the measured radio admission rate without the sustained frame corruption observed at `460800`. A 4 KB UART receive ring and matching bounded control line prevent focused-tracking configuration responses from displacing their HTTP acknowledgement. The host forwarding deadline remains 8 seconds and the firmware ACK deadline remains 12 seconds. Eligible GATT Device Name and Device Information reads use one persistent worker, strong-candidate admission, cooldown and heap guards, and a six-second operation budget. GATT results upload separately against the original boot-scoped observation ID and never delay raw advertisement delivery. The reader does not force pairing; protected values are reported as `security_required`.

When the backend assigns a focused tracking session, the existing continuous scanner also captures duplicate advertisements for only the accepted address/address-type pairs of the selected logical device. It emits dedicated RSSI samples every 200 ms at most and uploads them separately from normal observations. A valid current focus sample refreshes presence for that exact already-accepted identity, including median-filtered RSSI and return state. It cannot create a device, run correlation, infer movement, or move a durable location anchor.

The current firmware is BLE-only. Bluetooth Classic inquiry, Remote Name Request, A2DP discovery, and SDP are not implemented. A Classic-only TWS device can therefore be absent from this system.

## Normal Operation

Expected startup lines include:

```text
Bluetooth Scanner ready
Mode: real ESP32 over USB serial.
[serial] ESP32 serial bridge connected. Waiting for BLE scan frames.
```

The scanner becomes online after a valid heartbeat reaches the backend. USB presence alone does not make a scanner online.

Stop the runner with one `Ctrl+C`. The server closes SSE connections, terminates the serial bridge, releases the process lock, and leaves the ESP32 port available for flashing or diagnostics.

## Location Semantics

The backend uses a durable location anchor:

- a new logical device is anchored to the scanner-coordinate snapshot at observation time;
- changing scanner coordinates alone does not alter any device record;
- every newer accepted BLE observation snapshots the latest reported position of its scanner, including observations from the same scanner after that scanner moves;
- heartbeat and scanner-position updates never move a device without a BLE observation;
- a missing or offline device remains at its last observation anchor;
- delayed observations cannot rewind the current anchor;
- the RSSI-derived radius remains an uncertainty model around that snapshot.

When the bundled test console opens on the scanner host, it immediately starts a persistent browser `watchPosition` for `LOCAL_SCANNER_ID`. Every valid Safari/macOS Location Services fix is posted through the dedicated scanner-position endpoint with its source timestamp and reported accuracy. This live position path does not increment firmware configuration. The last reported fix remains available with its age and accuracy when Safari has not emitted a newer callback; it is never replaced by an IP-derived or fabricated coordinate.

With one scanner, left, right, forward, floor, room, and exact device coordinates cannot be derived from RSSI. Movement status means radio-sequence change, not a measured trajectory.

## Focused Signal Finder

The test console can start a focused session from a stored BLE device. This is an operator-guided search surface backed by dedicated tracking APIs, not a direction-finding solver.

- Fixed mode keeps all measurements at the scanner-location snapshot captured when the session starts.
- Walk mode records browser geolocation alongside RSSI while the scanner is moved. The browser device must remain physically co-located with the cable-connected ESP32; otherwise the path is not scanner-position evidence.
- A stronger RSSI meter or tone means that the accepted advertisement was received more strongly at the scanner. It does not identify a bearing.
- Only one target can use a scanner at a time. The browser renews a 30-second lease and Stop releases the assignment.
- Reloading and starting the same stored device renews the existing session. A different device receives a conflict until the first session is stopped or expires.

The map displays measurement anchors and paths. It never writes Walk positions into scanner installation coordinates or the device's normal location anchor.

## Identity And Presence Semantics

A raw observed identity represents an address and its captured Bluetooth evidence. A logical device represents the backend's current continuity decision.

- Stable/public addresses can retain normal present, missing, offline, and returned states.
- Unresolved random/private addresses expire as `identity_expired`; they are not presented as confirmed offline physical devices.
- The Devices and Location views hide unresolved manufacturer-only random broadcasts by default. A random advertiser with a directly captured Local Name remains visible as a named candidate, but it is not promoted to durable identity.
- Bluetooth SIG Company Identifier identifies a manufacturer-data namespace, not necessarily the product manufacturer or owner.
- A raw-verified `0x3081`, `0x3082`, or `0x3083` serial-profile advertisement is labeled `Confirmed Flipper Zero` with its black, white, or transparent hardware variant. The API also returns the rule ID, verification scope, and evidence used for the label.
- Generic HID, Nordic UART, device names, and address prefixes do not independently trigger the Flipper Zero label.
- Similar names, RSSI, service UUIDs, company IDs, and payload layouts do not automatically prove physical identity.
- Direct GATT names and model values improve display information but are not automatically unique identifiers.
- Apple Continuity TLVs, short-lived tag carryover, Handoff IV continuity, time, RSSI, and GATT model can produce an auditable possible-match proposal. This path never auto-merges or claims confirmed physical identity.

## Development Commands

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q backend run.py serial_bridge.py setup_project.py tests
node --check dashboard/app.js
.venv/bin/python -m platformio run --project-dir firmware
```

Run a specific test module while changing processing behavior:

```bash
.venv/bin/python -m pytest tests/test_processing.py -q
.venv/bin/python -m pytest tests/test_tracking.py -q
```

Any model change requires an Alembic migration. `run.py` applies migrations automatically, while production deployment should run migration as a distinct release step before application traffic is enabled.

## Production Position

SQLite is appropriate for one local runner with a modest observation rate. PostgreSQL is the supported production direction for multiple concurrent scanner hosts, larger retention windows, or remote deployment.

The bundled test console intentionally has no login and must not be deployed as a production frontend. Scanner APIs use bearer tokens, and scanner registration can require `SCANNER_REGISTRATION_SECRET`. Production deployments require HTTPS, network access control, and a separately designed client if interactive access is needed.

The current ESP32 transport is USB serial. A scanner in another location requires a host computer at that location running `serial_bridge.py` against a reachable central backend, or a separately implemented and tested direct network transport. The supplied firmware does not currently upload HTTPS by itself.

## Repository Hygiene

`.gitignore` excludes local secrets, databases, firmware configuration, virtual environments, build output, runtime locks, logs, local documentation, and reference projects. Only root `README.md` and `CHANGELOG.md` are repository-tracked Markdown. Runtime backend modules, migrations, setup and runner entry points, transport code, and automated tests remain tracked project code.
