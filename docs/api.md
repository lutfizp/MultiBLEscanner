# HTTP API

## Conventions

The backend serves JSON under `/api`. Timestamps are ISO 8601 and effective observation timestamps are normalized to UTC. `/dashboard/` is an optional development test console, not part of the production client contract.

Scanner endpoints use:

```http
Authorization: Bearer <scanner-token>
Content-Type: application/json
```

The path scanner ID must match the scanner associated with the token. Tokens are returned only during registration and are never included in scanner-list or diagnostics responses.

Operator/read endpoints currently have no application authentication. They must be protected at the network or reverse-proxy boundary when deployed outside a trusted host.

## Status And Runtime

### `GET /api/health`

Returns process availability:

```json
{"status": "ok"}
```

This route does not prove scanner or database freshness. Use diagnostics and scanner state for operational health.

### `GET /api/runtime-config`

Returns safe browser runtime values:

```json
{"app_timezone": "Asia/Jakarta"}
```

Secrets and database configuration are excluded.

## Scanner Registration

### `POST /api/scanners/register`

Registers a hardware identity and returns its scanner token once. When `SCANNER_REGISTRATION_SECRET` is configured, include:

```http
X-Registration-Secret: <registration-secret>
```

Request:

```json
{
  "hardware_id": "usb-esp32-002",
  "display_name": "Bekasi Scanner",
  "firmware_version": "esp32-ble-scanner-1.3.1",
  "hardware_version": "esp32dev",
  "installation_name": "bekasi-gateway"
}
```

`hardware_id` is required, unique, and 3-160 characters. The other fields are optional.

Response:

```json
{
  "scanner_id": "scn_0123456789ab",
  "token": "one-time-plain-token",
  "config_version": 1,
  "config": {
    "version": 1,
    "scan_interval_ms": 5000,
    "upload_interval_seconds": 5,
    "batch_size": 40,
    "rssi_min": -85,
    "presence_missing_seconds": 45,
    "presence_offline_seconds": 180,
    "extra": {}
  }
}
```

Store the token on the bridge host. Registration does not create observations or make the scanner online.

## Scanner Runtime Endpoints

### `POST /api/scanners/{scanner_id}/heartbeat`

Records one immutable health sample and updates current scanner state. `message_id` is the retry idempotency key for a scanner.

```json
{
  "message_id": "hb-boot-id-104",
  "scanner_time": "2026-07-15T08:10:00.000Z",
  "uptime_seconds": 683,
  "firmware_version": "esp32-ble-scanner-1.3.1",
  "network_state": {"transport": "usb_serial", "connected": true},
  "health": {"free_heap": 132448, "boot_id": "boot-a9c2"},
  "buffer_usage": 8,
  "pending_observations": 8,
  "dropped_observations": 0,
  "config_version": 1,
  "config_status": "applied"
}
```

Unknown fields are rejected. Counters must be non-negative. The response reports acceptance and current configuration state.

### `GET /api/scanners/{scanner_id}/config`

Returns the current runtime configuration for the authenticated scanner. The USB bridge forwards this response to firmware with `@@BT_SCANNER_CONFIG@@`.

### `POST /api/scanners/{scanner_id}/observations/batch`

Accepts 1-500 BLE observations. `batch_id` and `observation_id` values must remain stable across retries.

Batch fields:

| Field | Requirement | Meaning |
| --- | --- | --- |
| `batch_id` | Required | Scanner-generated batch retry identifier |
| `sent_at` | Required for USB-synchronized time | Scanner send time |
| `scanner_time` | Optional | Direct scanner clock value |
| `time_source` | Optional enum | `usb_host_synchronized`, `ntp_synchronized`, or `unsynchronized` |
| `boot_id` | Required for USB-synchronized time | Changes on firmware boot |
| `batch_sequence` | Optional non-negative integer | Ordering within one boot |
| `clock_sync_age_ms` | Optional | Age of the last host/NTP synchronization |
| `firmware_version` | Optional | Capture firmware provenance |
| `scanner_uptime_seconds` | Optional | Uptime at batch creation |
| `network_state` | Optional object | Scanner transport state |
| `dropped_observations` | Optional, default 0 | Queue overflow counter |
| `observations` | Required | Non-empty array, maximum 500 |

Observation fields:

| Group | Fields |
| --- | --- |
| Identity | `observation_id`, `address`, `address_type`, `advertised_name`, `local_name` |
| Time | `observed_at`, `scanner_time`, `time_source`, `boot_id`, `monotonic_ms`, `scan_cycle`, `clock_sync_age_ms` |
| Radio | `rssi`, `tx_power`, `advertising_type`, `connectable` |
| Structured AD | `service_uuids`, `service_data`, `manufacturer_data`, `appearance`, `advertising_flags` |
| Raw capture | `raw_advertising_payload`, `raw_scan_response_payload`, packet lengths, `payload_layout_version` |
| Optional direct enrichment | `gatt_enrichment` |

`rssi` is required and must be between -127 and 20 dBm. Hex values contain complete bytes and may omit the `0x` prefix. Unknown fields are rejected.

For `payload_layout_version: 2`, the backend verifies:

- `advertising_packet_length` equals the ADV payload byte length;
- `scan_response_packet_length` equals the scan-response byte length;
- `packet_length` equals their sum.

For `time_source: usb_host_synchronized`, an observation requires `observed_at`, `boot_id`, and `monotonic_ms`. A legacy frame with `scanner_time` but no `observed_at` is normalized to the direct scanner timestamp before validation.

Example abbreviated observation:

```json
{
  "observation_id": "obs-boot-a9c2-77",
  "observed_at": "2026-07-15T08:10:01.250Z",
  "scanner_time": "2026-07-15T08:10:01.250Z",
  "time_source": "usb_host_synchronized",
  "boot_id": "boot-a9c2",
  "monotonic_ms": 683250,
  "scan_cycle": 31,
  "clock_sync_age_ms": 12340,
  "address": "52:e3:88:83:e3:12",
  "address_type": "random",
  "rssi": -71,
  "advertising_type": "adv_ind",
  "connectable": true,
  "raw_advertising_payload": "02010603030a18",
  "raw_scan_response_payload": "0a094465766963652031",
  "advertising_packet_length": 7,
  "scan_response_packet_length": 10,
  "packet_length": 17,
  "payload_layout_version": 2
}
```

The service returns accepted, duplicate, ignored, and failed item counts plus item-level results. An HTTP 200 batch can therefore contain an item failure recorded in `processing_errors`. A top-level invalid JSON or schema returns HTTP 422 before service processing.

## GATT Enrichment

`gatt_enrichment` is optional and is stored separately from advertisement inference. Its `status` is one of:

- `success`
- `partial`
- `connection_failed`
- `service_discovery_failed`
- `security_required`

Direct fields include device name, manufacturer, model, serial, firmware/hardware/software revisions, System ID, PnP ID, discovered services, and raw characteristic values. Binary values are lowercase hexadecimal after validation. At most 64 characteristic values and 128 discovered services are accepted. Absence does not imply an empty or unknown value was read.

## Backend Query Endpoints

### `GET /api/overview`

Returns aggregate scanner state, confirmed present-device metrics, unresolved identity counts, observation rate, system health, and recent events. Unresolved rotating identities are kept separate from physical-device counts.

### `GET /api/devices`

Query parameters:

| Parameter | Behavior |
| --- | --- |
| `status` | Exact status; special value `present` includes active, newly detected, and returned |
| `scanner_id` | Restricts current logical anchor scanner |
| `include_ignored` | Includes operator-ignored logical devices |
| `include_expired` | Includes expired unresolved random identities |

Default ordering is newest `last_seen_at` first. Records include identity basis, presence trackability, direct and inferred device fields, latest capture provenance, signal/radial model, anchor coordinates, and status.

### `GET /api/devices/{device_id}`

Returns:

- current serialized logical device;
- raw observed identities and manufacturer evidence;
- up to 100 recent observations;
- up to 100 location estimates;
- up to 100 events;
- up to 100 identity-correlation records;
- up to 100 GATT enrichment records.

A correlation `proposal` is review evidence only. Only accepted evidence or a manual action can alter logical ownership.

### `GET /api/scanners`

Returns all scanners with location, configuration version, heartbeat/connectivity state, firmware, uptime, and current operational counters. Token hashes and plaintext tokens are excluded.

### `GET /api/events`

Optional filters are `event_type`, `scanner_id`, `device_id`, and `limit`. `limit` is clamped to 1-500. Events are returned newest first.

### `GET /api/settings`

Returns backend policy records with value, description, and update timestamp. These records are an engineering API, not test-console controls. A setting being present does not prove a consumer or scheduled worker exists; see the engineering guide for implemented consumers.

### `GET /api/diagnostics`

Returns server/database status, observation/event/error counts, identity counts, latest heartbeat counters, and up to 20 recent processing errors. It does not return secrets or full raw request bodies.

### `GET /api/live/events`

Opens a `text/event-stream` connection. Messages notify clients to refresh durable state. The stream is best effort and sends keepalive records; clients must recover by querying normal HTTP endpoints.

## Backend Operator Endpoints

### `PATCH /api/scanners/{scanner_id}`

Accepted fields are display name, enabled state, building, floor, room, zone, latitude, longitude, indoor coordinates, orientation, and maintenance notes. Unknown fields are rejected. A successful patch increments `config_version`.

The API does not infer scanner GPS. Coordinates come from operator/browser input and should represent the scanner installation.

### `PATCH /api/settings`

Request:

```json
{
  "values": {
    "presence_missing_seconds": 45,
    "presence_offline_seconds": 180
  }
}
```

The current endpoint stores arbitrary keys and therefore remains an engineering endpoint. The bundled test console does not expose it. New settings require explicit schema validation and a confirmed backend consumer before they are exposed operationally.

### `POST /api/devices/correlation`

Accepted `action` values are `merge`, `split`, `mark_known`, `mark_ignored`, and `unignore`.

```json
{
  "source_logical_device_id": "source-uuid",
  "target_logical_device_id": "target-uuid",
  "observed_identity_id": null,
  "action": "merge",
  "reason": "Operator-confirmed serial number"
}
```

Merge requires a target. Every action creates an audit record and event. Split currently records/reactivates the source and does not automatically reconstruct observation ownership from a previous merge.

## Error Semantics

| Status | Meaning |
| --- | --- |
| `400` | Semantically invalid manual operation or bridge-rejected frame |
| `401` | Missing, malformed, or invalid scanner token |
| `403` | Missing or incorrect registration secret |
| `404` | Scanner or logical device not found |
| `422` | JSON/schema validation failure |
| `500` | Unhandled server/database failure |

Scanner clients must retry transport and 5xx failures with backoff and stable identifiers. They must not change batch/observation IDs on retry. Validation failures require a firmware or payload fix; replaying the same invalid document cannot succeed.
