# Engineering Guide

## Purpose And Scope

Bluetooth Scanner is a local-first monitoring system for Bluetooth Low Energy advertisements collected by ESP32 scanners. The current deployment consists of one ESP32 connected to the backend host over USB. The data model and scanner-facing API support multiple scanners, but the current firmware does not contain a direct Wi-Fi or internet transport.

The backend preserves radio observations, derives cautious device state, records meaningful transitions, and exposes current and historical state through HTTP and server-sent events. It is not an indoor GPS system, a Bluetooth Classic inquiry tool, or a guarantee of permanent physical-device identity.

The native files under `dashboard/` are a development test console. They exercise backend endpoints and provide hardware-debug visibility, but they are not a production frontend and do not define business behavior.

The implementation follows four evidence classes:

| Class | Meaning | Examples |
| --- | --- | --- |
| Observed | Present in a received scanner payload or parsed raw AD structure | address, RSSI, local name, service UUID, manufacturer bytes |
| Derived | Deterministic processing with recorded method and source data | SIG company, category, signal band, RSSI window metric |
| Estimated | Model output with explicit limitations | radial distance baseline, movement classification, identity proposal |
| Operator supplied | Human-maintained deployment data | scanner coordinate, alias, notes, approved correlation token rule |

Missing radio data remains missing. The backend does not synthesize names, coordinates, packets, identities, observations, or device status.

## Capability Boundary

### Current Required Behavior

- Scan BLE advertisements and active-scan responses with ESP32/NimBLE.
- Preserve separate raw advertisement and scan-response payloads.
- Parse valid AD structures without allowing one malformed structure to stop the batch.
- Attempt bounded GATT enrichment for connectable devices without forcing pairing.
- Synchronize firmware observations to host UTC while retaining monotonic and boot metadata.
- Authenticate scanner API requests, deduplicate retries, and persist raw observations.
- Maintain raw radio identities separately from inferred logical devices.
- Track scanner health, device presence, signal change, radial proximity evidence, and events.
- Anchor a device to the scanner coordinate from its latest accepted location-changing observation.
- Keep the last anchor when a device becomes missing or offline.
- Move the anchor only after a later accepted observation belongs to a different scanner, or when the existing anchor has no coordinates.
- Expose current state, history, controls, diagnostics, and location evidence through backend APIs.

### Approximate Behavior

- RSSI signal bands describe received strength at a scanner, not physical distance bands.
- The log-distance value is a literature baseline and is useful only as radial evidence.
- RSSI window change can support probable movement; it cannot establish direction or a path.
- Random-address correlation can produce evidence-backed proposals. It cannot universally recover a permanent identity.
- A device that disappears is reported as not observed or offline by timeout. The system cannot prove that Bluetooth was disabled.

### Unsupported Claims

- An exact device coordinate from one scanner.
- Left, right, forward, or backward direction from one RSSI stream.
- A permanent physical identity from a random address alone.
- Detection of Bluetooth Classic-only devices.
- Direct internet upload from the current firmware.
- Durable firmware buffering across ESP32 power loss. The current queue is bounded RAM.

### Future Multi-Scanner Work

The schema already stores scanner coordinates, observation provenance, per-scanner RSSI, location estimates, and chronological events. A future coordinate solver can consume those records without changing the observation contract. It will still require synchronized fixed anchors, measured site data, and a method appropriate to the environment, such as fingerprinting or calibrated multilateration.

## Runtime Topology

```text
BLE advertisers
      |
      v
ESP32 NimBLE scanner
      |
      | framed JSON over USB serial
      v
serial_bridge.py
      |
      | authenticated HTTP on loopback
      v
FastAPI backend ---- SQLAlchemy ---- SQLite or PostgreSQL
      |
      +---- server-sent events ---- optional development test console
```

`run.py` starts the FastAPI process and USB bridge in one local runner. It also holds a process lock so two local runners do not write to the same SQLite database or compete for one serial device.

For a remote USB scanner, deploy `serial_bridge.py` on a host physically connected to that ESP32 and point the bridge at a reachable central backend. The host must protect the scanner token and use HTTPS across untrusted networks. Direct ESP32 networking requires a separate firmware transport implementation.

## Repository Map

| Path | Responsibility |
| --- | --- |
| `run.py` | Local process lock, environment loading, database migration, backend lifecycle, USB bridge lifecycle |
| `setup_project.py` | Virtual environment, dependencies, `.env`, SQLite bootstrap, firmware config, build, and upload |
| `serial_bridge.py` | Serial discovery, frame validation, host time sync, authentication header, HTTP forwarding, acknowledgements |
| `backend/app/main.py` | FastAPI routes, dependencies, periodic state refresh, optional test-console mount |
| `backend/app/config.py` | Environment schema and project-relative path resolution |
| `backend/app/database.py` | SQLAlchemy engine/session and SQLite concurrency pragmas |
| `backend/app/models.py` | Durable relational model |
| `backend/app/schemas.py` | Scanner and operator API request validation |
| `backend/app/services.py` | Ingestion orchestration, state transitions, correlation, serialization, diagnostics |
| `backend/app/processing.py` | Pure timestamp, address, RSSI, proximity, movement, and identity functions |
| `backend/app/bluetooth_ad.py` | Raw BLE AD-structure parser |
| `backend/app/bluetooth_sig.py` | Bluetooth SIG company lookup from local data |
| `backend/app/device_intelligence.py` | Manufacturer payload and category derivation |
| `backend/app/correlation.py` | Token extraction and RSSI-time assignment mathematics |
| `backend/app/realtime.py` | In-process server-sent event broker |
| `backend/app/security.py` | Scanner token generation, hashing, and verification |
| `backend/app/seed.py` | Default local scanner bootstrap and explicit real-data cleanup helpers |
| `backend/migrations/` | Alembic migration environment and ordered schema revisions |
| `dashboard/` | Non-production test console and vendored Leaflet runtime |
| `firmware/` | PlatformIO ESP32 firmware |
| `tests/` | Unit and integration-style backend tests |

## Backend Function Catalog

This catalog identifies the supported change points. Leading-underscore functions are internal implementation details and should be changed through their owning public service unless a new tested abstraction is required.

### Configuration And Database

| Function or property | Responsibility |
| --- | --- |
| `Settings.project_root` | Stable repository root derived from source location |
| `Settings.data_path` | Absolute local data directory resolved from project-relative configuration |
| `Settings.resolved_database_url` | Explicit database URL or generated SQLite URL |
| `get_settings` | Cached environment-backed settings instance |
| `ensure_sqlite_directory` | Creates the configured SQLite parent directory |
| `engine_options` | Selects PostgreSQL defaults or serialized local SQLite pool options |
| `get_db` | FastAPI session dependency with guaranteed close |

### HTTP Lifecycle

`main.py` keeps route handlers thin. `require_scanner` parses and verifies bearer credentials. `refresh_runtime_state_once` and `refresh_runtime_states` own periodic device/scanner timeout evaluation. Route functions delegate to services: `register`, `heartbeat`, `scanner_config`, `observations_batch`, `api_overview`, `api_devices`, `api_device_detail`, `api_manual_correlation`, `api_scanners`, `api_patch_scanner`, `api_events`, `api_settings`, `api_patch_settings`, `api_diagnostics`, and `live_events`.

Business rules must remain in service or processing modules so HTTP and future transports behave consistently.

### Scanner Services

| Function | Responsibility |
| --- | --- |
| `ensure_default_settings` | Inserts missing supported system settings and removes obsolete signal settings |
| `register_scanner` | Creates a scanner or rotates the token for an existing hardware ID |
| `authenticate_scanner` | Verifies scanner enabled state and token hash |
| `get_scanner_config` | Returns or creates the scanner's current configuration row |
| `scanner_config_payload` | Produces the firmware-safe configuration document |
| `record_heartbeat` | Deduplicates heartbeat, stores health, updates online state, and emits connection event |
| `refresh_scanner_states` | Applies heartbeat/observation timeout and emits disconnect event |
| `serialize_scanner` | Produces a secret-free scanner API record |
| `list_scanners` | Returns serialized scanners in display order |
| `patch_scanner` | Applies operator installation fields and increments configuration version |

### Observation Services

| Function | Responsibility |
| --- | --- |
| `canonicalize_observation_payload` | Re-parses raw AD payloads and records source/conflict/error provenance |
| `effective_observation_time` | Selects trusted scanner time or server receive time |
| `find_or_create_observed_identity` | Maintains direct address/fingerprint identity history |
| `find_or_create_logical_device` | Selects the logical record under conservative identity rules |
| `rssi_samples_for_window_metric` | Loads chronological per-scanner RSSI windows |
| `processing_settings_from_config` | Resolves presence thresholds for one scanner |
| `proximity_model_payload` | Serializes model constants, range status, and RSSI evidence |
| `process_observation` | Executes one complete observation transaction unit |
| `process_batch` | Deduplicates and isolates item processing for a scanner batch |
| `create_event` | Creates evidence-rich, deduplicated events |
| `commit_allowing_event_dedupe_race` | Treats a concurrent dedupe-key collision as an already-created event |

### Identity Correlation Services

| Function | Responsibility |
| --- | --- |
| `correlation_config` | Loads and bounds correlation policy values |
| `_identity_observations` | Loads chronological evidence for one raw identity |
| `_trusted_rssi_points` | Selects time-trusted RSSI samples |
| `_identity_token_evidence` | Extracts approved scoped token hashes from observations |
| `_record_identity_correlation` | Persists proposal or accepted evidence |
| `_merge_accepted_identity_correlation` | Moves accepted identity history into the canonical logical record |
| `run_identity_correlation` | Executes token carryover and optional RSSI-time review assignment |
| `apply_manual_correlation` | Audits merge, split, known, ignored, and unignore actions |

### Current-State And Query Services

| Function | Responsibility |
| --- | --- |
| `refresh_presence_states` | Applies missing, offline, and unresolved-identity expiry transitions |
| `latest_location_estimates` | Gets one latest location estimate per logical device |
| `latest_observations` | Gets one latest observation per logical device |
| `latest_device_enrichments` | Gets one latest GATT record per logical device |
| `presence_identity_bases` | Classifies whether current identity evidence supports durable presence |
| `serialize_device` | Combines logical state with latest raw/model/enrichment provenance |
| `serialize_recent_observation` | Produces inspectable observation history without losing processing notes |
| `serialize_event` | Produces event timeline records |
| `serialize_identity_correlation` | Produces auditable proposal/acceptance evidence |
| `overview` | Calculates scanner/device API metrics and recent activity |
| `list_devices` | Applies status/scanner/ignored/expired filters and serializes current devices |
| `device_detail` | Loads bounded identity, observation, location, event, correlation, and enrichment history |
| `list_events` | Filters and bounds event history |
| `get_settings_values` | Returns settings with descriptions and update time |
| `patch_settings` | Stores setting values; consumers remain responsible for semantic bounds |
| `diagnostics` | Returns safe health, volume, identity, heartbeat, and processing-error summaries |

### Pure Processing And Parsing

| Function | Responsibility |
| --- | --- |
| `ensure_utc` | Converts aware/naive timestamps to UTC with explicit fallback |
| `normalize_address`, `normalize_hex` | Canonical textual radio values |
| `is_randomized_address` | Uses reported address type to flag private/random identities |
| `is_synthetic_address_pattern` | Rejects known sequential/repeated fake address structures |
| `signal_band_from_rssi` | Descriptive received-strength category |
| `signal_band_confidence` | Margin from signal-band boundaries, not location confidence |
| `estimate_journal_distance_m` | Literature-baseline log-distance model |
| `infer_proximity_from_rssi` | Combines radial model and RSSI sequence reliability |
| `rssi_window_metrics` | Published consecutive-window signal-change calculation |
| `evaluate_presence_status`, `observed_again_status` | Pure presence transition decisions |
| `identity_signature`, `identity_fingerprint` | Stable evidence representation, not automatic physical identity proof |
| `parse_ad_payload` | Parses one raw AD byte stream with offsets and errors |
| `parse_advertising_and_scan_response` | Reconciles separate ADV/scan-response structures with provenance |
| `company_identifiers` | Cached local Bluetooth SIG company table |
| `company_identifier_from_manufacturer_data`, `company_name_from_manufacturer_data`, `company_identifier_hex` | Company ID extraction and display from manufacturer AD bytes |
| `analyze_manufacturer_data` | Evidence-scoped manufacturer payload interpretation |
| `infer_device_category` | Conservative service/name/payload category inference |
| `parse_find_my_payload`, `parse_airdrop_payload` | Recognizes supported Apple payload layouts |

### Correlation Mathematics

`fit_linear_rssi_regression` and `rssi_regression_difference` calculate predecessor signal behavior. `akiyama_pair_cost` combines time and RSSI residuals. `percentile` and `alpha_from_p90_overlap` reproduce deployment scaling. `minimum_cost_assignment` and `assign_akiyama_pairs` solve global candidates with unmatched choices. `parse_token_rules` validates operator protocol rules, and `extract_approved_tokens` hashes scoped AD bytes.

### Realtime, Security, And Bootstrap

`RealtimeBroker.start`, `publish`, `stream`, and `request_shutdown` provide best-effort process-local SSE. `generate_scanner_token`, `hash_scanner_token`, and `verify_scanner_token` own scanner credentials. `ensure_local_scanner` creates only the configured scanner/configuration; cleanup helpers in `seed.py` are explicit maintenance functions and are not called by normal startup.

## Bootstrap And Configuration

`python3 setup_project.py backend` is the supported first-install command. It performs these operations in order:

1. Creates `.venv` with the invoking Python interpreter.
2. Installs backend dependencies from `requirements.txt`.
3. Creates `.env` only where values are absent and generates local secrets with `secrets.token_urlsafe`.
4. Creates `firmware/include/config.h` with the configured scanner ID.
5. Resolves the database URL.
6. Runs Alembic to `head`.
7. Ensures the configured local USB scanner exists without inserting observations.

The script preserves existing secrets and settings. A legacy root database configured as `sqlite:///bluetooth_scanner.sqlite3` is kept in place and normalized to `BLUETOOTH_SCANNER_DATA_DIR=.`. It is not copied, deleted, or silently replaced.

Configuration precedence is:

1. Process environment.
2. Values in `.env`.
3. Defaults in `backend/app/config.py`.

`DATABASE_URL` is an optional full SQLAlchemy URL override. Without it, the database is `<project root>/<BLUETOOTH_SCANNER_DATA_DIR>/bluetooth_scanner.sqlite3`. A relative SQLite override is resolved from the project root, not the shell working directory. This makes `run.py` behave consistently when launched from another directory.

The `.env.example` file documents every runtime variable. Secrets belong in `.env`, never in firmware source or diagnostics responses.

## Backend Lifecycle

At startup, `run.py` re-executes itself through `.venv/bin/python` when the project virtual environment exists. It loads `.env`, acquires `.bluetooth-scanner.lock`, applies Alembic migrations, initializes system settings, and ensures the local scanner record exists. Uvicorn then starts FastAPI and the serial bridge starts after the backend is listening.

FastAPI startup creates a five-second maintenance task. Each cycle evaluates device presence and scanner connectivity. SQLite busy errors skip that cycle instead of terminating the application. Shutdown signals the maintenance task and serial bridge, closes the server, and removes the owned lock file.

`Base.metadata.create_all` is retained as a defensive bootstrap after migration, but schema evolution must be implemented through Alembic. It is not a substitute for migrations.

## Scanner Transport

The firmware emits one request frame in this format:

```text
|||BRIDGE_START|||
POST
/api/scanners/<scanner-id>/observations/batch
{...JSON body...}
|||BRIDGE_END|||
```

The bridge accepts only supported methods and scanner paths, parses JSON before forwarding, rejects semantically empty observation batches, adds `Authorization: Bearer <token>`, and sends the backend result to the firmware.

Control records use line prefixes:

| Prefix | Direction | Purpose |
| --- | --- | --- |
| `@@BT_SCANNER_TIME@@` | host to ESP32 | UTC epoch milliseconds |
| `@@BT_SCANNER_ACK@@` | host to ESP32 | backend HTTP status for queue acknowledgement |
| `@@BT_SCANNER_CONFIG@@` | host to ESP32 | current runtime scanner configuration |

The serial parser is chunk-based and tolerates partial UTF-8 input. A disconnect closes the current port and retries discovery. On macOS, CP2102 hardware normally appears as `/dev/cu.usbserial-*`; `ESP32_SERIAL_PORT=auto` selects a compatible port.

## Firmware Behavior

The current firmware uses NimBLE active scanning. It stores advertisement and scan-response bytes separately, reports address type and advertising metadata, and sends at most 12 observations in one serial frame to keep JSON allocation bounded during NimBLE and GATT activity.

Observations are queued in a fixed-size RAM ring. When full, the oldest record is removed and `dropped_observations` is incremented. A batch remains pending until a successful backend acknowledgement; retries reuse identifiers. Queue contents do not survive reset or power loss, so the current implementation meets temporary host-disconnection buffering but not durable offline storage.

One eligible connectable target is queued for GATT enrichment per scan cycle. Reads are limited by what the peripheral exposes without pairing. Connection failure, service-discovery failure, and security requirements are explicit statuses rather than missing-value substitutions.

Runtime scanner configuration is pulled through the bridge. Compile-time firmware configuration contains the scanner ID and hardware-safe constants only. The scanner token stays on the bridge host.

## Observation Contract And Ingestion

The ingestion path is intentionally ordered:

1. Authenticate the scanner token.
2. Validate the batch and observation schema.
3. Reject a scanner ID mismatch.
4. Normalize raw fields and parse separate ADV/scan-response AD structures.
5. Select a trusted effective timestamp.
6. Reject known synthetic address patterns.
7. detect an existing observation ID and return an idempotent duplicate result.
8. Find or create the raw observed identity.
9. Find or create the logical device without assuming random-address permanence.
10. Insert the immutable observation and optional GATT enrichment.
11. Calculate signal, radial distance, RSSI-window, and movement evidence.
12. Update current logical state only when observation ordering permits it.
13. Insert the chronological location estimate.
14. Create deduplicated device/scanner events for meaningful transitions.
15. Commit valid items and record item-level failures without discarding the rest of the batch.

Batch processing is tolerant by design. Pydantic rejects malformed top-level input with HTTP 422. Once a valid batch enters service processing, each observation is handled independently and a failure is stored in `processing_errors`.

### Time Trust

USB-synchronized data is trusted only when it contains `observed_at`, `boot_id`, `monotonic_ms`, and a clock-sync age no greater than five minutes. Future timestamps more than five minutes beyond server receive time are not used for ordering. Untrusted scanner time remains in provenance while `server_received_at` becomes the effective `observed_at`.

`boot_id`, monotonic time, scan-cycle sequence, batch sequence, clock-sync age, and time-source decisions are stored in observation processing notes. This supports restart detection and delayed/out-of-order analysis without rewriting raw scanner timestamps.

## Raw And Processed Data

`ObservedIdentity` and `Observation` preserve scanner evidence. `LogicalDevice`, `DeviceLocationEstimate`, and `DeviceEvent` contain processed state. Updating a device status never overwrites historical observations.

Raw AD parsing uses length/type/value structures from both payloads. Recognized structures include flags, names, service UUIDs, service data, Tx Power, appearance, connection interval, target addresses, advertising interval, and manufacturer-specific data. Unknown structures remain in parse provenance. Duplicate or conflicting structures produce parse notes rather than invented resolution.

Bluetooth SIG company attribution is derived only from a valid company identifier in manufacturer-specific AD data. A random address prefix is not treated as an organizationally unique MAC prefix. Vendor fields in API responses therefore represent SIG payload evidence, not address ownership.

## Identity Model

A raw identity is the exact address/address-type and fingerprint evidence received from the radio. A logical device is the operator-facing record that may contain one or more accepted raw identities.

Public/static identities can be tracked more directly. Random/private addresses remain separate unless one of these evidence paths is accepted:

- An operator-approved scoped AD token of at least 40 bits appears consistently and uniquely.
- An RSSI-time assignment is promoted under a deployment policy validated with local labelled data.
- An operator manually merges records.

Names, ordinary service UUID sets, manufacturer company IDs, similar RSSI, and location alone are insufficient for automatic identity carryover. Statistical correlation is a review proposal by default. It does not move a location or suppress a record until accepted.

The Akiyama-style assignment uses time gap and regression residual cost with explicit unmatched candidates. This avoids forcing every rotating address into a predecessor. Settings define its search window and evidence thresholds, not a universal identity guarantee.

Manual actions are audited in `manual_device_correlation_decisions` and produce events. Merge rewrites observation ownership to the target logical record and marks the source merged. Split currently records the decision and reactivates the source; it does not reconstruct previously rewritten ownership automatically. Engineering work that expands split behavior must define which observations or identities move and add a migration-safe audit trail.

## Presence And Offline Records

Presence applies to logical devices with a trackable identity basis. Unresolved rotating addresses are not counted as confirmed physical devices indefinitely; they age to `identity_expired` and are hidden by default from the main list.

Configured timers produce cautious state transitions:

- `temporarily_missing` after the missing threshold.
- `offline` after the offline threshold.
- `returned` when a previously absent logical device is observed again.
- scanner `offline` when neither heartbeat nor observation activity remains within the scanner timeout.

An offline device record is not a heartbeat record. Heartbeats are stored in `scanner_heartbeats`; device status is stored on `logical_devices`. Events use deduplication keys so maintenance polling does not create the same transition each cycle.

## Location And Movement

### Durable Location Anchor

The map coordinate is the scanner coordinate captured for the device's current accepted location anchor. It is not the Bluetooth transmitter's exact coordinate.

The anchor update policy is chronological:

- A first accepted observation stores the scanner ID, zone, coordinates, and `location_anchor_observed_at`.
- Later observations from the same scanner update signal evidence but do not drag an existing coordinate when the scanner coordinate is edited or the scanner is moved.
- A later accepted observation from a different scanner moves the anchor to that scanner's coordinate.
- An older delayed observation cannot rewind the anchor.
- Missing and offline transitions do not change the anchor.
- Accepted identity carryover can move the canonical logical device; a correlation proposal cannot.

This supports the intended Tebet-to-Bekasi flow while retaining the Tebet observation and location history.

### Signal And Distance

The backend stores raw RSSI and calculates a received-signal band using fixed descriptive thresholds. The radial model uses:

```text
d = 10 ^ ((A - RSSI) / (10 * n))
```

The current literature baseline is `A = -47 dBm` and `n = 2`. Values beyond the paper's approximately four-metre clear-line-of-sight evaluation range remain model output but are marked outside the validated range. They must not be interpreted as measured distance.

One scanner produces a circle of possible positions, not a point. The map centers the uncertainty ring on the stored scanner anchor and never invents a bearing.

### Movement

Movement evidence compares two chronological five-reading RSSI windows per scanner. The implementation records means, absolute change, scanner weights, a `tanh` metric, reliability, threshold, and contributing anchor count. A window is not ready until ten readings exist. Small changes remain stationary; a threshold crossing can produce `probably_moving` with explicit RSSI-only provenance.

RSSI change may be caused by the transmitter, scanner, people, doors, obstruction, orientation, or multipath. The state is therefore not labelled confirmed physical movement. Direction and route require additional sensors or multiple fixed scanners with validated geometry.

## Database Model

| Table | Purpose | Important integrity rule |
| --- | --- | --- |
| `monitored_locations` | Reusable building, floor, room, zone, and coordinates | Stable location identifier |
| `scanners` | Persistent scanner identity, installation, location, health state | Unique hardware ID; token stored as hash |
| `scanner_configurations` | Versioned current scan/upload thresholds | One current row per scanner |
| `scanner_heartbeats` | Immutable health samples | Unique scanner/message ID |
| `observed_identities` | Raw BLE identity and latest direct fields | Indexed address/type and fingerprint |
| `logical_devices` | Current operator-facing state and durable anchor | Indexed status, last seen, primary address |
| `observations` | Immutable received BLE samples | Unique scanner/batch/item and time indexes |
| `device_enrichments` | Direct GATT read results | Unique scanner/source observation/transport |
| `device_location_estimates` | Chronological signal/radial evidence | Device/time index |
| `device_events` | Device and scanner transitions | Unique dedupe key |
| `manual_device_correlation_decisions` | Operator identity decisions | Immutable action record |
| `device_identity_correlations` | Proposal/accepted evidence between identities | Unique predecessor/successor/method |
| `system_settings` | Runtime processing policy | Setting key primary key |
| `processing_errors` | Item-level pipeline failures | Time index for diagnostics |

Schema changes require a new Alembic revision, updated ORM model, updated schema/serializer as applicable, and tests against both a fresh database and an upgraded database.

## SQLite And PostgreSQL

SQLite is the supported single-host default. The engine creates the database parent directory, enables foreign keys, WAL journal mode, a 30-second busy timeout, normal synchronous mode, and one pooled application connection. The process lock prevents two `run.py` writers. External SQLite writers can still cause lock contention and should not be used during scanning.

PostgreSQL is the deployment path for multiple bridge hosts, concurrent operators, or sustained write volume. Set `DATABASE_URL`, apply migrations once during deployment, and run a single maintenance scheduler unless the background task is moved to a coordinated worker. Multiple Uvicorn workers currently duplicate the periodic maintenance loop, although event dedupe protects many transitions; use one worker until scheduler ownership is separated.

Retention values are stored and exposed as settings, but there is currently no automatic retention worker. Database growth must be monitored and cleanup must not be advertised as active until a tested archival/deletion job is implemented. Any cleanup must preserve events, identity decisions, and required summaries while deleting raw observations in bounded transactions.

## API And Security

Scanner registration optionally requires `X-Registration-Secret`. Scanner heartbeat, configuration, and observation endpoints require a bearer token. Only a salted hash is stored. The local scanner token remains in `.env` on the bridge host.

Operator/read endpoints currently have no application login. They expose device observations, scanner positions, notes, and operational controls, so network access must be restricted by host firewall, reverse proxy, VPN, or trusted LAN policy. Internet deployments require HTTPS. Diagnostics must never return tokens, registration secrets, environment values, or raw authorization headers.

The API contract is documented in `docs/api.md`. HTTP 401 indicates missing or invalid scanner authentication, 403 indicates a registration-secret failure, 404 indicates an unknown resource, and 422 indicates schema validation failure.

## Realtime And Test Console Boundary

The backend's durable client contract is REST plus server-sent event notifications. SSE tells clients to refresh; it does not replace database state or response schemas.

`RealtimeBroker` is in-process and best effort. Each subscriber has a queue of 100 messages; stale/slow subscribers can lose older UI notifications and recover through normal HTTP refresh. SSE is not the source of durable state. The database is authoritative.

The bundled `dashboard/` client is retained only for backend and hardware testing. It must not acquire business rules, duplicate inference, expose internal tuning controls, or be treated as a production frontend. A future production client must consume the documented API and implement its own authentication and authorization boundary.

## Extension Procedures

### Adding An Observation Field

1. Establish whether the value is observed, parsed, derived, estimated, or operator supplied.
2. Add strict input validation in `schemas.py` only if the scanner sends it.
3. Preserve direct source bytes or provenance needed to reproduce it.
4. Add model columns through a new Alembic revision when durable querying requires them.
5. Update `canonicalize_observation_payload` and `process_observation` without changing older raw records.
6. Update serializers, API documentation, firmware payload, and regression tests. Update the test console only when inspection of the field is useful.

### Adding A Scanner Setting

1. Decide whether the setting is backend-wide, scanner runtime, or compile-time hardware safety.
2. Add backend-wide defaults to `Settings` or `DEFAULT_SETTINGS`; add scanner runtime fields to `ScannerConfiguration`.
3. Validate ranges at the API boundary.
4. Include scanner runtime values in `scanner_config_payload` and implement firmware application with a configuration version.
5. Document restart requirements and failure fallback.

### Adding An Event

Create events only on a state transition or operationally meaningful occurrence. Include previous/new state, scanner/device references, reason, confidence, evidence details, and a stable dedupe key. Do not create events during serialization or client polling.

### Adding A Device Classifier

Classifiers belong in `device_intelligence.py`. They must consume direct advertisement or enrichment evidence, expose that evidence, and return no classification when the payload is ambiguous. Address prefixes must not identify vendors for random/private addresses.

### Adding A Location Solver

Implement a new method alongside existing estimates. Store method name, input scanner IDs, timestamps, model parameters, confidence/uncertainty region, and validation state. Never overwrite the raw per-scanner observations. Require at least the anchor count and geometry expected by the method, and return no coordinate when the constraints are not met.

### Adding A Transport

Keep the scanner-facing HTTP contract and idempotency identifiers stable. A network firmware or gateway should implement bounded storage, retry with backoff, acknowledgement, clock provenance, token protection, and duplicate-safe replay. The backend must not depend on scanners sharing a LAN.

### Adding A Second Scanner

Create a unique scanner ID/hardware ID/token, assign a fixed physical location, deploy a bridge host or network transport, verify synchronized timestamps, and validate overlapping observations. Do not enable automatic random-address acceptance or coordinate multilateration merely because a second scanner exists; both require labelled deployment evidence.

## Testing Strategy

Pure processing and correlation mathematics are tested independently of the database. API and service tests use isolated SQLite files and must not connect to the operational database. Setup tests use temporary directories and never overwrite the project `.env` or firmware configuration.

Every change should select tests by risk:

- Parsing changes: valid, truncated, duplicate, unknown, and conflicting AD structures.
- Timestamp changes: stale sync, future skew, reset, delayed batch, and out-of-order observation.
- Identity changes: public address, random address, unmatched assignment, proposal, accepted token, manual merge, and false-match protection.
- Location changes: first anchor, same-scanner edit, different-scanner move, offline preservation, and delayed no-rewind.
- Database changes: fresh migration, upgrade migration, constraints, duplicate retry, rollback, and SQLite lock behavior.
- Firmware changes: PlatformIO build plus real hardware serial, scan, queue, retry, reconnect, time-sync, and payload validation.
- Test-console changes: JavaScript syntax and a narrow smoke check against real backend responses; console behavior is outside backend acceptance.

No automated test or development helper may feed generated Bluetooth observations into the operational runtime.

## Operational Invariants

The following conditions must remain true after any change:

- A raw observation is never replaced by inferred state.
- Retry identifiers remain stable and duplicates do not create observations or events.
- A malformed item does not terminate the remaining valid batch.
- A random address is not treated as a permanent physical identity.
- A correlation proposal does not merge records or move an anchor.
- Offline state does not move a device coordinate.
- Same-scanner observations do not drag an established coordinate.
- Delayed observations do not rewind current state.
- SIG company attribution comes from payload company ID, not random MAC prefix.
- One-scanner RSSI never produces a bearing or exact point.
- Scanner secrets never appear in operator or diagnostics responses.
- Setup never inserts fake observations and never deletes an existing database implicitly.

## Known Engineering Gaps

- Firmware buffering is volatile RAM rather than durable bounded flash storage.
- The current firmware transport is USB only.
- Retention settings do not yet execute cleanup.
- Operator/read endpoints have no application authentication in the current backend contract.
- Manual split does not reconstruct observations from a prior merge.
- The in-process SSE broker does not distribute events across multiple backend processes.
- Multi-scanner coordinate estimation and floor-plan solving are not implemented.
- Scanner configuration editing currently covers only fields exposed by the backend API, not every firmware constant.

These gaps must be treated as explicit backlog items rather than capabilities implied by the schema.
