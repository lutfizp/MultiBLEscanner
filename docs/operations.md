# Operations Guide

## Deployment Profiles

| Profile | Database | Scanner transport | Intended use |
| --- | --- | --- | --- |
| Local USB | SQLite | ESP32 USB to local bridge | Current installation and engineering work |
| Central service | PostgreSQL | One bridge host per USB scanner location | Multiple sites and concurrent operators |
| Direct network scanner | PostgreSQL | Separate network-capable firmware | Future implementation, not supplied |

Operator/read APIs currently have no application login. Local binding is the default. Any wider exposure requires a trusted network, firewall, VPN, or reverse proxy access policy. The bundled browser console is for testing only.

## First Installation

Install the backend, create local configuration, initialize SQLite, build firmware, and flash the connected ESP32:

```bash
python3 setup_project.py all
```

The command creates `.venv`, installs pinned dependency ranges, generates missing local secrets, writes `firmware/include/config.h`, applies Alembic migrations, creates the local scanner record, builds firmware, auto-detects the serial port, and uploads it.

It does not insert Bluetooth observations, generate device records, or delete an existing database.

Use narrower setup targets when appropriate:

```bash
python3 setup_project.py backend
python3 setup_project.py firmware
python3 setup_project.py flash
python3 setup_project.py flash --port /dev/cu.usbserial-0001
```

`firmware` builds without modifying the board. `flash` and `all` upload to the connected board. Close `run.py`, PlatformIO Monitor, Arduino Serial Monitor, and every other serial client before upload.

An existing prepared environment can skip package installation:

```bash
python3 setup_project.py backend --skip-dependencies
```

This option expects `.venv` and all required packages to already exist.

## Normal Start And Stop

Connect the flashed ESP32 and run:

```bash
python3 run.py
```

The runner automatically re-executes with `.venv/bin/python`, applies pending migrations, binds the first free port from `RUN_PORT` through `RUN_PORT + 19`, starts FastAPI, and starts the USB bridge.

Healthy startup includes:

```text
Bluetooth Scanner ready
Mode: real ESP32 over USB serial.
[serial] ESP32 serial bridge connected. Waiting for BLE scan frames.
```

The scanner becomes online only after a valid heartbeat reaches the backend. A USB device appearing in the operating system is not sufficient.

Stop with one `Ctrl+C`. The runner closes server-sent event streams, terminates the bridge process, and releases `.bluetooth_scanner.run.lock`. Do not kill the terminal during normal shutdown unless the process no longer responds.

## Environment Management

`.env` is local secret and runtime state. `setup_project.py` adds missing values but preserves existing values. `.env.example` is the reviewed template.

Important variables:

| Variable | Purpose |
| --- | --- |
| `BLUETOOTH_SCANNER_DATA_DIR` | Project-relative or absolute local data directory |
| `DATABASE_URL` | Optional complete SQLAlchemy database override |
| `SCANNER_REGISTRATION_SECRET` | Protects new scanner registration |
| `SCANNER_TOKEN_SALT` | Salt for scanner token hashes |
| `LOCAL_SCANNER_*` | Identity and initial installation metadata for the USB scanner |
| `APP_TIMEZONE` | Timezone exposed to API clients for display |
| `RUN_HOST`, `RUN_PORT` | Local HTTP listener |
| `ESP32_SERIAL_ENABLED` | Enables the local USB bridge |
| `ESP32_SERIAL_PORT` | `auto` or an explicit serial device |
| `ESP32_SERIAL_BAUD` | Firmware serial rate, currently 115200 |

Restart the runner after changing environment variables. Runtime system settings stored in the database do not replace environment values that are read during process startup.

## SQLite Operations

Fresh installations use `<project>/data/bluetooth_scanner.sqlite3`. The parent directory is created automatically. Relative paths are resolved from the project root.

The local engine enables WAL, foreign keys, a 30-second busy timeout, and a single pooled connection. Only one `run.py` process may own the database and serial bridge. The lock error includes the owning PID.

Confirm runner and port ownership on macOS:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
lsof /dev/cu.usbserial-0001
```

Do not modify the SQLite database with a second writer while scanning. A read-only inspection connection is safer, but long-lived readers can delay WAL checkpointing.

### SQLite Backup

Stop `run.py`, then use the SQLite backup command against the configured file:

```bash
sqlite3 data/bluetooth_scanner.sqlite3 ".backup 'bluetooth_scanner.backup.sqlite3'"
```

When `BLUETOOTH_SCANNER_DATA_DIR=.` is retained from a legacy installation, the source file is `bluetooth_scanner.sqlite3` at the project root. Back up `.env` separately through a secure secret-management process; the database contains only token hashes and cannot recreate a bridge token.

### SQLite Restore

1. Stop the runner and verify no process owns the database.
2. Preserve the current database as a rollback copy.
3. Place the restored database at the configured path.
4. Run `python3 setup_project.py backend --skip-dependencies` to apply newer migrations.
5. Start `python3 run.py` and inspect diagnostics before reconnecting additional scanners.

Never restore only `-wal` or `-shm` sidecar files. Use a consistent backup generated by SQLite.

## PostgreSQL Deployment

`docker-compose.yml` provides a development PostgreSQL 16 service. Its default credentials are for local development and must be changed for any shared environment.

```bash
docker compose up -d postgres
```

Set a deployment-specific URL:

```dotenv
DATABASE_URL=postgresql+psycopg://bluetooth:strong-password@127.0.0.1:5432/bluetooth_scanner
```

Apply migrations before application traffic:

```bash
.venv/bin/alembic -c backend/alembic.ini upgrade head
```

Run one Uvicorn worker until periodic maintenance ownership is moved to a coordinated scheduler. Multiple workers each start the five-second maintenance task and use independent in-process SSE brokers.

Production controls:

- terminate TLS at a maintained reverse proxy;
- expose scanner APIs only over HTTPS;
- restrict operator/read API network access;
- do not deploy the bundled test console as a production frontend;
- use unique scanner tokens and a strong registration secret;
- rotate a compromised scanner token by provisioning a replacement scanner credential;
- back up PostgreSQL with tested `pg_dump`/restore procedures;
- monitor connection count, database size, write latency, invalid payloads, and scanner freshness;
- run migrations as an explicit release step.

## Firmware Build And Flash

The automated build uses the project virtual environment:

```bash
python3 setup_project.py firmware
```

Equivalent direct build:

```bash
.venv/bin/python -m platformio run --project-dir firmware
```

Flash explicitly:

```bash
python3 setup_project.py flash --port /dev/cu.usbserial-0001
```

The generated `firmware/include/config.h` contains the scanner ID. The bearer token is not compiled into firmware; it remains in `.env` on the bridge host.

After flashing, start `run.py` and verify this sequence:

1. serial port opens;
2. configuration request receives HTTP 200;
3. heartbeat receives HTTP 200;
4. observation batches receive HTTP 200;
5. scanner status becomes online;
6. diagnostics counters increase without processing-error growth.

## USB Diagnosis

List ports:

```bash
.venv/bin/python -m serial.tools.list_ports -v
```

Typical ESP32 USB-UART descriptors include CP2102/CP210x, CH340, USB UART, and `/dev/cu.usbserial-*` or `/dev/cu.SLAB_USBtoUART` device names.

`[Errno 6] Device not configured` on macOS means the opened serial device disappeared or reset at the operating-system level. The bridge closes and retries. Check:

- USB data cable condition;
- hub and adapter stability;
- board power;
- another serial monitor owning the port;
- repeated ESP32 resets after firmware upload;
- whether the port name changes after reconnect.

An unplugged scanner correctly remains offline after the heartbeat timeout. Reconnecting the USB cable is not enough if firmware does not emit valid heartbeat frames.

## Scanner Location Assignment

The ESP32 has no GPS. Scanner coordinates are backend installation data supplied through `PATCH /api/scanners/{scanner_id}`. The bundled test console can use browser geolocation as a development helper, but production provisioning should use a controlled backend client or deployment process.

Before moving a physical scanner to another site, use a new scanner identity when historical device anchors must remain attached to the old site. Reusing the same scanner ID and editing its coordinates does not drag established device anchors by design, but it also cannot distinguish two physical installations cleanly in scanner history.

## Additional Scanner Deployment

The supplied firmware requires a USB bridge host at each location.

1. Assign a unique scanner ID, hardware ID, and token.
2. Build firmware with that scanner ID.
3. Install a bridge host at the scanner location.
4. Store the token on that host and configure a reachable central backend URL.
5. Use HTTPS and network access controls outside a trusted LAN.
6. Assign fixed scanner coordinates and location metadata.
7. Confirm time sync, heartbeat, observation upload, retry behavior, and duplicate suppression.
8. Validate overlapping scans before enabling any multi-scanner inference.

`serial_bridge.py` can be run independently on the remote host:

```bash
.venv/bin/python serial_bridge.py \
  --base-url https://scanner.example.internal \
  --port auto
```

Set `LOCAL_SCANNER_TOKEN` through protected environment configuration or service-manager secrets. Do not pass a token through shared shell history. The scanner ID in each request path comes from firmware configuration and must match the provisioned token.

## Data Volume And Retention

Each accepted packet creates an observation and may create location evidence. High device density, short scan cycles, and long raw retention increase write rate and database size.

The database currently stores retention settings but does not run an automatic deletion worker. Operations must monitor file/table growth. A future retention job must use bounded transactions, preserve events and manual decisions, aggregate useful history, and report every cleanup run. Lowering a setting today does not delete existing rows.

## Upgrade Procedure

1. Stop the runner or remove application traffic.
2. Back up the database and protected environment configuration.
3. Update source and dependency lock/ranges through the approved release process.
4. Run `python3 setup_project.py backend`.
5. Build firmware and flash only when firmware changed.
6. Run the automated test suite.
7. Start one runner and inspect `/api/health`, `/api/diagnostics`, scanner heartbeat, and one real observation batch.
8. Record the release in `CHANGELOG.md`.

Database downgrade is not the normal rollback path. Restore the pre-upgrade backup when a migration cannot be safely reversed.

## Troubleshooting Matrix

| Symptom | Interpretation | Action |
| --- | --- | --- |
| Runner reports another active PID | Process lock is held | Verify PID, stop the owner normally, then retry |
| Scanner remains offline | No valid heartbeat reached backend | Inspect serial connection, firmware frame, token, scanner ID, and backend response |
| `Device not configured` | USB device detached/reset | Stabilize cable/power, close other monitors, allow bridge rediscovery |
| Port exists but is busy | Another process owns serial | Use `lsof`, stop monitor/bridge/uploader |
| HTTP 401 from scanner endpoint | Token missing or mismatched | Reconcile `.env`, scanner record, and bridge arguments |
| HTTP 422 batch | JSON parsed but schema/length/time rule failed | Read validation path, fix firmware payload, retain stable IDs |
| Bridge drops malformed JSON | Serial frame was incomplete/corrupted | Confirm current firmware build and serial stability |
| SQLite `database is locked` | Competing writer or long transaction | Stop duplicate tools/processes; do not add concurrent local writers |
| Expected TWS absent | Device may advertise Bluetooth Classic only | Put target into a BLE-advertising mode and verify with another BLE scanner |
| Device count appears high | Rotating random addresses are visible | Inspect raw identities and expiry; do not merge merely to reduce count |
| Company appears incorrect | Company ID namespace is being interpreted as product vendor | Inspect raw manufacturer AD bytes and parser provenance |
| Distance appears implausible | Literature baseline is outside its validated environment/range | Treat as radial model output; inspect RSSI and physical obstructions |
| Offline point remains on map | Last accepted anchor is intentionally retained | A newer accepted observation at another scanner moves it |
| Test console stops live refreshing | SSE is disconnected or queue notification was lost | Reload; durable backend state remains available through HTTP |

## Incident Data

Collect these items without exposing secrets:

- runner startup and serial log around the failure;
- scanner ID, firmware version, boot ID, and config version;
- operating-system serial-port listing and ownership;
- `/api/diagnostics` output;
- affected observation IDs, batch IDs, and validation path;
- database type, migration revision, and file/table size;
- exact deployment topology and scanner coordinate history.

Do not include `.env`, bearer tokens, registration secrets, raw authorization headers, or unrelated captured device payloads in public reports.
