# Project Structure

```text
.
├── run.py                         Local backend and USB bridge runner
├── setup_project.py               Environment, database, build, and flash setup
├── serial_bridge.py               USB serial to authenticated HTTP gateway
├── requirements.txt               Backend and test dependencies
├── requirements-firmware.txt      PlatformIO dependency
├── docker-compose.yml             Development PostgreSQL service
├── .env.example                   Runtime configuration template
├── backend/
│   ├── alembic.ini                Alembic logging and script configuration
│   ├── app/
│   │   ├── main.py                FastAPI routes and lifecycle
│   │   ├── config.py              Environment and path resolution
│   │   ├── database.py            Engine, sessions, and SQLite pragmas
│   │   ├── models.py              SQLAlchemy tables and relationships
│   │   ├── schemas.py             Pydantic request validation
│   │   ├── services.py            Ingestion and application services
│   │   ├── tracking.py            Focus leases, assignments, samples, positions, and cleanup
│   │   ├── processing.py          Pure signal, time, and identity functions
│   │   ├── correlation.py         Address-rotation correlation mathematics
│   │   ├── bluetooth_ad.py        BLE AD-structure parser
│   │   ├── bluetooth_sig.py       SIG company lookup
│   │   ├── device_intelligence.py Manufacturer and category interpretation
│   │   ├── realtime.py            Server-sent event broker
│   │   ├── security.py            Scanner token functions
│   │   ├── seed.py                Local scanner bootstrap and explicit cleanup
│   │   └── data/
│   │       └── bluetooth_sig_companies.json
│   └── migrations/
│       ├── env.py                 Runtime database URL integration
│       └── versions/               Ordered schema revisions
├── dashboard/                     Non-production backend test console
│   ├── index.html                 Inspection views, device drawer, and Signal Finder
│   ├── app.css                    Test-console layout
│   ├── app.js                     API inspection, tracking SSE, and map behavior
│   └── vendor/leaflet/            Locally served map runtime
├── firmware/
│   ├── platformio.ini             ESP32 board and library definition
│   ├── include/
│   │   ├── config.example.h       Reviewed compile-time template
│   │   └── config.h               Generated local scanner ID, ignored by Git
│   └── src/main.cpp               NimBLE scan, focused/GATT workers, queues, time, and bridge frames
├── tests/
│   ├── test_processing.py         Signal, presence, location, and parsing behavior
│   ├── test_correlation.py        Correlation mathematics and evidence rules
│   ├── test_dashboard.py          Automatic test-console geolocation wiring
│   ├── test_realtime.py           SSE shutdown and broker behavior
│   ├── test_serial_bridge.py      Frame parsing and bridge validation
│   ├── test_tracking.py           Focus-session isolation, leases, samples, and positions
│   └── test_setup.py              Path-safe and non-destructive setup behavior
├── docs/                          Engineering and operations documentation
├── data/                          Default generated SQLite directory, ignored
└── references/                    Read-only upstream implementation references
```

## Ownership Boundaries

The firmware owns radio capture, bounded temporary buffering, scanner-side provenance, and the dedicated serial transport task. It does not decide logical identity, final presence, or map coordinates.

The serial bridge owns transport framing, host time synchronization, scanner authentication attachment, malformed JSON rejection, and reconnect behavior. It does not alter valid observation content.

The backend owns durable state, idempotency, parsing, inference, correlation, current-state transitions, events, diagnostics, and scanner configuration.

`tracking.py` owns the high-rate operator-guided measurement channel. It does not call full advertisement processing; current exact-target samples may refresh presence/RSSI for their already-linked identity but cannot create records, infer movement, run correlation, or move anchors.

The `dashboard/` directory is a backend test console. It is never the source of truth, must not reproduce inference, and must not expose internal processing or calibration controls.

Alembic owns schema evolution. ORM model edits without a corresponding migration are incomplete.

## Change Placement

| Change | Primary location | Required companion work |
| --- | --- | --- |
| New captured BLE field | firmware, `schemas.py`, `services.py` | migration if queryable, API docs, tests |
| New AD parser | `bluetooth_ad.py` | provenance tests and serializer update |
| New device inference | `device_intelligence.py` | direct evidence output and ambiguity tests |
| New signal model | `processing.py` | method metadata, service integration, scientific basis, tests |
| New identity evidence path | `correlation.py`, `services.py` | false-link controls, audit record, operator visibility |
| New table or column | `models.py`, migrations | fresh/upgrade tests and serializers |
| New endpoint | `main.py`, `schemas.py`, `services.py` | API docs, authorization decision, tests |
| New scanner transport | gateway or firmware module | retry, idempotency, time, token, offline policy |
| New test-console inspection | `index.html`, `app.css`, `app.js` | only when needed to verify an existing backend contract |

Detailed invariants and extension procedures are maintained in `docs/engineering-guide.md`.
