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
- Proposal-only default behavior.
- Accepted carryover and chronological location continuity.
- Protection against name/vendor/UUID-only automatic merging.

### Persistence And State

- Stable batch and observation retry identifiers.
- Duplicate heartbeat and observation behavior.
- One bad service-processed observation does not discard valid items.
- Current state cannot be rewound by delayed observations.
- First location anchor capture.
- Same-scanner coordinate edit does not drag an anchor.
- Newer accepted different-scanner observation moves an anchor.
- Offline state preserves the last location.
- Scanner heartbeat timeout and recovery.
- Event deduplication.
- Manual action audit records.

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
.venv/bin/python -m pytest tests/test_serial_bridge.py -q
.venv/bin/python -m pytest tests/test_realtime.py -q
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
6. Disconnect USB long enough to exercise retry and scanner-offline behavior.
7. Reconnect and confirm queue replay preserves IDs and does not duplicate observations.
8. Fill or stress the bounded queue and confirm dropped count reporting.
9. Change scanner coordinates and confirm existing same-scanner device anchors do not move.
10. Observe an accepted logical device at a second scanner and confirm only the current anchor moves while history remains.

Do not use fabricated advertiser frames as evidence that real scanning works.

## Optional Test Console Smoke Check

The browser console exists only to inspect backend behavior during development. A small smoke check is sufficient:

- no clipped navigation, headings, table cells, dialogs, or controls;
- markers remain clickable at dense zoom levels;
- active, missing, offline, ignored, and scanner markers are visually distinct;
- selecting a marker opens the matching device without requiring pixel-perfect targeting;
- long names, addresses, UUIDs, and payloads wrap or scroll inside their owner;
- loading, API error, disconnected SSE, zero-device, and missing-coordinate states are legible;
- map wording identifies scanner-anchor uncertainty and never claims exact position.

Do not block a backend release on visual polish outside this narrow test surface. Backend schemas, persistence, processing, migrations, transport, and hardware behavior are the release contract.

## Failure Reporting

A useful test failure record includes the command, environment profile, database type, migration revision, scanner firmware version, failing assertion or HTTP validation path, and the smallest non-secret evidence required to reproduce it. Tokens and `.env` contents are excluded.
