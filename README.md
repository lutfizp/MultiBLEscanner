# Bluetooth Scanner

Bluetooth Scanner is a backend service for Bluetooth Low Energy observations collected by one or more ESP32 scanners. The current transport is an ESP32 connected to a host computer over USB serial. The host forwards scanner heartbeats, configuration requests, and observation batches to FastAPI, which owns validation, persistence, correlation, presence state, location anchors, events, and diagnostics.

`dashboard/` is a bundled test console for inspecting backend responses during development and hardware verification. It is not a production frontend or part of the backend contract.

The implementation preserves the distinction between measured data and inference:

- raw advertising and scan-response bytes are retained;
- parsed AD structures record their source and parse status;
- GATT values are stored separately as direct enrichment reads;
- randomized addresses remain separate unless an approved evidence path or operator decision links them;
- RSSI produces radial signal evidence, not a bearing or exact indoor coordinate;
- a device location point is the scanner-location snapshot where the device was observed, not the physical Bluetooth transmitter coordinate.

## Requirements

- Python 3.9 or newer on macOS or Linux. The local runner currently uses a POSIX process lock.
- An original ESP32-compatible `esp32dev` board for the supplied firmware.
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

Firmware release `esp32-ble-scanner-1.3.1` captures separate ADV and scan-response payloads, uses a bounded RAM queue, keeps IDs stable across retries, uploads at most 12 observations per serial frame, and attempts bounded GATT Device Name and Device Information reads for eligible connectable advertisers. It does not force pairing.

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
- changing the coordinates of the same scanner ID does not drag existing device anchors;
- a newer accepted observation by another scanner ID can move the logical device anchor;
- delayed observations cannot rewind the current anchor;
- the RSSI-derived radius remains an uncertainty model around that snapshot.

With one scanner, left, right, forward, floor, room, and exact device coordinates cannot be derived from RSSI. Movement status means radio-sequence change, not a measured trajectory.

## Identity And Presence Semantics

A raw observed identity represents an address and its captured Bluetooth evidence. A logical device represents the backend's current continuity decision.

- Stable/public addresses can retain normal present, missing, offline, and returned states.
- Unresolved random/private addresses expire as `identity_expired`; they are not presented as confirmed offline physical devices.
- Bluetooth SIG Company Identifier identifies a manufacturer-data namespace, not necessarily the product manufacturer or owner.
- Similar names, RSSI, service UUIDs, company IDs, and payload layouts do not automatically prove physical identity.
- Direct GATT names and model values improve display information but are not automatically unique identifiers.

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
```

Any model change requires an Alembic migration. `run.py` applies migrations automatically, while production deployment should run migration as a distinct release step before application traffic is enabled.

## Production Position

SQLite is appropriate for one local runner with a modest observation rate. PostgreSQL is the supported production direction for multiple concurrent scanner hosts, larger retention windows, or remote deployment.

The bundled test console intentionally has no login and must not be deployed as a production frontend. Scanner APIs use bearer tokens, and scanner registration can require `SCANNER_REGISTRATION_SECRET`. Production deployments require HTTPS, network access control, and a separately designed client if interactive access is needed.

The current ESP32 transport is USB serial. A scanner in another location requires a host computer at that location running `serial_bridge.py` against a reachable central backend, or a separately implemented and tested direct network transport. The supplied firmware does not currently upload HTTPS by itself.

## Documentation

- `docs/engineering-guide.md`: backend architecture, module responsibilities, invariants, data model, processing, extension points, and maintenance procedures.
- `docs/api.md`: backend HTTP endpoints, authentication, payload rules, filtering, and response semantics.
- `docs/operations.md`: backend installation, firmware transport, deployment, backup, recovery, second-scanner deployment, and troubleshooting.
- `docs/testing.md`: backend, migration, transport, and real-hardware validation.
- `docs/calibration.md`: RSSI evidence, published model provenance, and physical limits.
- `docs/correlation.md`: address-rotation evidence paths and acceptance policy.
- `docs/privacy.md`: authorized use and operational privacy boundaries.
- `docs/project-structure.md`: source tree ownership.
- `CHANGELOG.md`: phase-by-phase implementation and validation history.

## Repository Hygiene

`.gitignore` excludes local secrets, databases, firmware configuration, virtual environments, build output, runtime locks, logs, and vendor/reference Markdown. Root `README.md`, `CHANGELOG.md`, and backend documentation under `docs/` are repository-eligible. `docs/phase-1-requirement-analysis.md` and `docs/phase-2-technical-design.md` remain explicitly ignored. Git ignore rules do not untrack files that were committed before the rule existed.
