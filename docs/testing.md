# Testing Guide

## Test Environment

Prepare dependencies once:

```bash
python3 setup_project.py backend
```

Run the complete Python suite with the project interpreter:

```bash
.venv/bin/python -m pytest -q
```

Tests must use temporary files or isolated database fixtures. They must never use the operational `.env` database to insert generated observations.

## Static Validation

```bash
.venv/bin/python -m compileall -q backend run.py serial_bridge.py setup_project.py tests
node --check dashboard/app.js
```

The optional test console has no Node build step. `node --check` validates its JavaScript syntax only and is not part of backend behavioral coverage.

## Firmware Validation

Build without uploading:

```bash
python3 setup_project.py firmware
```

Equivalent direct command:

```bash
.venv/bin/python -m platformio run --project-dir firmware
```

A successful compile does not validate BLE behavior, serial framing, heap stability, or USB reconnects. Hardware verification remains mandatory after changes to firmware, bridge framing, scanner configuration, time synchronization, queue handling, or GATT enrichment.

## Coverage Map

### Processing

- UTC normalization and fallback ordering.
- Address normalization and synthetic-pattern rejection.
- Random/private-address classification.
- Signal-band boundaries.
- Journal-baseline radial distance output and validation range.
- Two-window RSSI change metric, weights, reliability, and readiness.
- Missing, offline, and returned transitions.
- Identity signatures and fingerprints.

### BLE Capture

- Separate advertisement and scan-response payloads.
- Complete, shortened, and conflicting local names.
- 16-bit, 32-bit, and 128-bit UUID byte order.
- Service data, manufacturer data, flags, Tx Power, and appearance.
- Unknown AD structures.
- Truncated length fields and malformed values.
- Packet-length consistency for payload layout version 2.
- GATT hex and collection limits.

### Identity Correlation

- Scoped approved-token extraction.
- Minimum token length and observation count.
- Unique predecessor requirement.
- RSSI regression residual cost.
- Per-run alpha calculation.
- Global minimum-cost assignment with explicit unmatched choices.
- Concatenated Apple Continuity TLV parsing, scoped token hashing, Handoff IV extraction, and truncated-frame handling.
- Apple transition proposals require protocol evidence or the bounded GATT/subtype/time/RSSI combination and never auto-merge.
- Proposal-only default behavior.
- Accepted carryover and chronological location continuity.
- Protection against name/vendor/UUID-only automatic merging.

### Persistence And State

- Stable batch and observation retry identifiers.
- Duplicate heartbeat and observation behavior.
- One bad service-processed observation does not discard valid items.
- Current state cannot be rewound by delayed observations.
- First location anchor capture.
- Scanner-coordinate update alone does not drag an anchor.
- Newer same-scanner BLE observation snapshots the latest scanner coordinate.
- Newer accepted different-scanner observation moves an anchor.
- Stale browser-position evidence retains coordinates and reports that it is not fresh.
- Offline state preserves the last location.
- Scanner heartbeat timeout and recovery.
- Event deduplication.
- Manual action audit records.

### Focused Tracking

- A session requires a real stored observation and an enabled assigned scanner.
- Targets are exact accepted address/address-type pairs from the logical device.
- One scanner cannot hold two different active targets.
- Repeating Start for the same device returns and renews one session.
- Sample IDs and Walk position IDs are idempotent.
- Unrelated identities are rejected without creating normal observations.
- Delayed and out-of-order samples do not drive live signal state.
- Current exact-target samples refresh presence and RSSI; they do not create identities, infer movement, run correlation, create normal location estimates, or move durable anchors.
- Fixed mode rejects Walk positions.
- Walk positions do not patch scanner installation coordinates.
- Topic SSE is isolated by session and retains the latest bounded samples.
- Stop and lease expiry produce terminal state and release scanner configuration.
- An assigned in-flight batch after Stop receives an acknowledged discard and creates no sample.

### Setup

- Default SQLite path resolves under the project root.
- Relative SQLite overrides do not depend on shell working directory.
- Existing legacy database configuration is preserved in place.
- Existing secrets are not regenerated.
- Firmware configuration contains the requested scanner ID.
- Setup does not create observations or delete data.

## Targeted Commands

```bash
.venv/bin/python -m pytest tests/test_processing.py -q
.venv/bin/python -m pytest tests/test_correlation.py -q
.venv/bin/python -m pytest tests/test_dashboard.py -q
.venv/bin/python -m pytest tests/test_serial_bridge.py -q
.venv/bin/python -m pytest tests/test_realtime.py -q
.venv/bin/python -m pytest tests/test_tracking.py -q
.venv/bin/python -m pytest tests/test_setup.py -q
```

Use a targeted command while iterating, then run the complete suite before the phase changelog is finalized.

## Migration Validation

Every schema change requires both paths:

1. Create an empty database and migrate from base to `head`.
2. Copy a representative pre-change database, migrate it to `head`, and verify counts, constraints, and current state.

For SQLite, verify foreign keys, WAL startup, duplicate constraints, and runner behavior. For PostgreSQL-facing changes, validate on PostgreSQL rather than assuming SQLite behavior is identical.

## Real Hardware Acceptance

Use real BLE advertisers and the flashed ESP32. The acceptance sequence is:

1. Start with a clean serial owner and run `python3 run.py`.
2. Confirm config request, heartbeat, and observation batches return HTTP 200.
3. Confirm scanner time provenance contains boot ID, monotonic time, and fresh sync age.
4. Confirm raw ADV and scan-response payloads match a second trusted BLE inspection tool for selected devices.
5. Confirm a known connectable device either yields direct GATT data or an explicit failure status.
6. During a slow or unavailable GATT target, confirm config, heartbeat, normal scan, and serial processing continue; the source observation must eventually contain `operation_timeout` rather than a fabricated value.
7. Start focused tracking while GATT is active and confirm the GATT result becomes `cancelled`, focus sampling starts, and no pairing prompt is initiated.
8. Disconnect USB long enough to exercise retry and scanner-offline behavior.
9. Reconnect and confirm queue replay preserves IDs and does not duplicate observations.
10. Fill or stress the bounded queue and confirm dropped count reporting.
11. Delay or reject one bridge request and confirm BLE scan cycles continue while heartbeat transport metrics identify the failed path.
12. Change scanner coordinates and confirm existing device anchors do not move without a BLE observation.
13. Observe the device again at that scanner and confirm its current anchor snapshots the new coordinate while history remains.
14. Observe an accepted logical device at a second scanner and confirm only the current anchor moves while history remains.
15. Select a real stored BLE device and start Fixed tracking.
16. Confirm firmware config contains only that device's accepted address/type, heartbeat reports the session, and dedicated focus batches return HTTP 200.
17. Confirm fresh samples reach tracking SSE and refresh presence while normal observation batches continue without duplicate logical-device inflation or location movement.
18. Reload the client, start the same device, and confirm the backend returns the same session ID.
19. Stop tracking and confirm firmware removes focus mode after configuration refresh.
20. For Walk mode, keep the browser physically co-located with the moving scanner, inspect GPS accuracy, and confirm dedicated Walk positions do not directly mutate normal device state.

Do not use fabricated advertiser frames as evidence that real scanning works.

## Optional Test Console Smoke Check

The browser console exists only to inspect backend behavior during development. A small smoke check is sufficient:

- no clipped navigation, headings, table cells, dialogs, or controls;
- markers remain clickable at dense zoom levels;
- active, missing, offline, ignored, and scanner markers are visually distinct;
- selecting a marker opens the matching device without requiring pixel-perfect targeting;
- selecting the same device from a row, marker, anchored list, or map selector opens one detail drawer;
- Fixed/Walk start, lease renewal, stale state, reload/resume, and Stop are usable without clipped controls;
- scanner-location tracking starts automatically at page load, survives temporary timeout/unavailable callbacks, reports secure-context and permission failures explicitly, and never substitutes IP-derived coordinates or fabricated accuracy;
- bridge/backend failure returns control before the presence-missing threshold, preserves the retry batch, and cannot create a one-minute global device disappearance;
- Safari geolocation timestamps outside a five-minute callback window fall back to callback receipt time while coordinates and reported accuracy remain unchanged;
- Signal Finder samples use its dedicated SSE and do not trigger a full dashboard refresh for every sample;
- long names, addresses, UUIDs, and payloads wrap or scroll inside their owner;
- loading, API error, disconnected SSE, zero-device, and missing-coordinate states are legible;
- map wording identifies scanner-anchor uncertainty and never claims exact position.

Do not block a backend release on visual polish outside this narrow test surface. Backend schemas, persistence, processing, migrations, transport, and hardware behavior are the release contract.

## Failure Reporting

A useful test failure record includes the command, environment profile, database type, migration revision, scanner firmware version, failing assertion or HTTP validation path, and the smallest non-secret evidence required to reproduce it. Tokens and `.env` contents are excluded.
