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
{
  "app_timezone": "Asia/Jakarta",
  "local_scanner_id": "scn_dev_lab_001"
}
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
  "firmware_version": "esp32-ble-scanner-1.6.8",
  "hardware_version": "esp32-d0wd-v3",
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
    "rssi_min": -110,
    "presence_missing_seconds": 45,
    "presence_offline_seconds": 180,
    "extra": {}
  }
}
```

Store the token on the bridge host. Registration does not create observations or make the scanner online.
`rssi_min=-110` is the practical firmware capture floor. Operator-facing visibility is controlled separately by identity evidence and the `include_transient` query flag.

## Scanner Runtime Endpoints

### `POST /api/scanners/{scanner_id}/heartbeat`

Records one immutable health sample and updates current scanner state. `message_id` is the retry idempotency key for a scanner.

```json
{
  "message_id": "hb-boot-id-104",
  "scanner_time": "2026-07-15T08:10:00.000Z",
  "uptime_seconds": 683,
  "firmware_version": "esp32-ble-scanner-1.6.8",
  "hardware_version": "esp32-d0wd-v3",
  "reset_reason": "power_on",
  "network_state": {"transport": "usb_serial", "connected": true},
  "health": {
    "free_heap": 132448,
    "boot_id": "boot-a9c2",
    "tracking_session_id": "",
    "tracking_state": "inactive",
    "gatt_worker_state": "idle",
    "gatt_worker_age_ms": 0,
    "pending_tracking_samples": 0,
    "dropped_tracking_samples": 0,
    "transport_request_sequence": 104,
    "transport_last_path": "/api/scanners/scn_dev_lab_001/config",
    "transport_last_status": 200,
    "transport_last_duration_ms": 41,
    "transport_timeout_count": 0,
    "transport_failure_count": 0,
    "serial_control_overflow_count": 0
  },
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

When the scanner has an active assignment, the response also contains:

```json
{
  "tracking_focus": {
    "session_id": "tracking-session-uuid",
    "mode": "fixed",
    "expires_at": "2026-07-29T07:20:29.629736Z",
    "sample_interval_ms": 200,
    "upload_interval_ms": 500,
    "target_identities": [
      {
        "observed_identity_id": "identity-uuid",
        "address": "80:e1:26:9e:3e:e3",
        "address_type": "public"
      }
    ]
  }
}
```

`tracking_focus` is absent when no live lease is assigned. The target list contains only raw identities already associated with the selected logical device.

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

Legacy observation payloads may include optional `gatt_enrichment`. Firmware `1.6.8` sends raw observations immediately and reports GATT separately through `POST /api/scanners/{scanner_id}/enrichments`:

```json
{
  "report_id": "gatt-obs-scn-101",
  "source_observation_id": "obs-scn-101",
  "enriched_at": "2026-08-10T03:24:20.100Z",
  "address": "24:11:11:b3:eb:ee",
  "address_type": "public",
  "gatt_enrichment": {
    "status": "success",
    "device_name": "Space Travel",
    "model_number": "TWS-01",
    "discovered_services": ["1800", "180a"],
    "characteristic_values": {"2a24": "5457532d3031"},
    "attempt_duration_ms": 412
  }
}
```

The source observation must already exist for the same authenticated scanner. A missing source returns HTTP 409 so firmware can retry; an address mismatch returns HTTP 400; an existing source/transport pair returns an idempotent duplicate response. The enrichment `status` is one of:

- `success`
- `partial`
- `connection_failed`
- `service_discovery_failed`
- `security_required`
- `operation_timeout`
- `cancelled`

`operation_timeout` means the peripheral did not complete within the six-second GATT operation budget. `cancelled` means an explicit scanner mode change, currently focused tracking, stopped the attempt. `security_required` records a protected characteristic without forcing pairing.

Direct fields include device name, manufacturer, model, serial, firmware/hardware/software revisions, System ID, PnP ID, discovered services, and raw characteristic values. Binary values are lowercase hexadecimal after validation. At most 64 characteristic values, 512 bytes per value, and 128 discovered services are accepted. Absence does not imply an empty or unknown value was read.

## Focused Tracking

Focused tracking is a separate measurement channel. It requires a logical device backed by a stored BLE observation and never accepts heartbeat data as a target. Focus samples are not full advertisement observations: they do not create identities or devices, run correlation, calculate normal movement state, or change the durable location anchor. A current in-order sample for an exact assigned identity refreshes that identity and logical device's `last_seen_at`, RSSI, observation count, and cautious return/active presence state.

### `POST /api/devices/{device_id}/tracking-sessions`

Starts or resumes a session:

```json
{"mode": "fixed"}
```

`mode` is `fixed` or `walk`. The backend assigns the scanner from the device's latest stored observation, snapshots that scanner's current coordinates, resolves up to eight accepted address/address-type targets, and increments scanner configuration.

One active session is permitted per scanner. Repeating this request for the same logical device renews and returns the existing session. A different device receives HTTP 409 until the assignment is stopped or its lease expires. Ignored devices and records without a real observation receive HTTP 400.

The response contains:

- session ID, logical device ID, mode, state, start/lease/expiry/end timestamps, stop reason, and summary;
- scanner assignments, accepted target identities, fixed coordinate snapshot, sample freshness, smoothed RSSI, and dropped-sample count;
- a 30-second `lease_seconds` value;
- the adaptive `sample_stale_seconds` value for each assignment and the session, bounded to 12-30 seconds;
- the backend signal scale, four-second median window, and four-versus-twelve-second trend windows.

Active state values are `arming`, `waiting_for_advertisement`, `live`, `stale`, `scanner_offline`, and `identity_changed`. Terminal states are `stopped` and `expired`.

### `GET /api/tracking-sessions/{session_id}`

Returns the session plus at most 200 recent focused samples and 500 Walk positions in chronological order. A focused sample includes its direct RSSI, backend-smoothed RSSI, normalized zero-to-one signal level, sequence, accepted observed-identity ID, and delayed flag.

### `POST /api/tracking-sessions/{session_id}/lease`

Renews an active session for 30 seconds. Clients should renew before expiry and treat HTTP 409 as terminal. Lease expiry stops focus mode even when a browser closes without sending Stop.

### `DELETE /api/tracking-sessions/{session_id}`

Stops the session and releases all scanner assignments. Optional query parameter `reason` is limited to 120 characters. The terminal summary records sample count, minimum/maximum/median RSSI, and the strongest measured sample; a nearby Walk position is included only when its timestamp is within ten seconds of that sample.

### `POST /api/tracking-sessions/{session_id}/positions`

Stores browser-provided geolocation evidence for a Walk session:

```json
{
  "position_id": "walk-unique-id",
  "scanner_id": "scn_dev_lab_001",
  "observed_at": "2026-07-29T07:20:00.000Z",
  "latitude": -6.26085,
  "longitude": 106.960005,
  "accuracy_m": 12.4
}
```

The scanner must be assigned to that session. Fixed sessions reject positions with HTTP 409. `position_id` is idempotent within a session. The endpoint does not patch scanner installation coordinates or logical-device anchors. The position is valid scanner-path evidence only when the browser providing geolocation remains physically co-located with the scanner.

### `GET /api/tracking-sessions/{session_id}/events`

Opens a topic-isolated `text/event-stream`. Event types are `tracking_sample`, `scanner_position`, and `session_state`, plus `connected` and `ping`. This stream is best effort; reconnect by reading the session resource and reopening the stream.

### `POST /api/scanners/{scanner_id}/tracking-samples/batch`

Scanner-authenticated endpoint for firmware focus batches:

```json
{
  "batch_id": "focus-batch-stable-id",
  "session_id": "tracking-session-uuid",
  "dropped_samples": 0,
  "samples": [
    {
      "sample_id": "focus-session-17",
      "observed_at": "2026-07-29T07:20:00.200Z",
      "boot_id": "boot-a9c2",
      "monotonic_ms": 683200,
      "sequence": 17,
      "address": "80:e1:26:9e:3e:e3",
      "address_type": "public",
      "rssi": -67
    }
  ]
}
```

A batch contains 1-64 samples. The backend accepts only exact normalized address and address-type pairs in the assignment, deduplicates `sample_id` per scanner, suppresses stale or out-of-order samples from live SSE, and still records accepted delayed samples with `delayed: true`. Current in-order samples are factual BLE presence evidence for the already-linked identity, but they do not contain a full ADV payload and never re-anchor the device. The response reports accepted, duplicate, rejected, discarded, session, and state counts.

An authenticated assigned scanner can have an immutable batch already in flight when Stop or lease expiry reaches the backend. Such samples are not inserted after the terminal transition; they are acknowledged with HTTP 200 and counted as `discarded` so firmware can release the retry batch. An unknown session or unassigned scanner remains an error.

## Backend Query Endpoints

### `GET /api/overview`

Returns aggregate scanner state, present BLE-record count, visible candidate count, trackable present-device metrics, unresolved identity counts, observation rate, system health, and recent events. `present_ble_records` counts all logical records currently in `active`, `newly_detected`, or `returned`; it must not be interpreted as a deduplicated physical-device count. `visible_device_candidates` applies the Devices/Locations admission rule, while `active_devices` remains limited to durable identity bases and `active_unresolved_identities` exposes the unresolved remainder.

### `GET /api/devices`

Query parameters:

| Parameter | Behavior |
| --- | --- |
| `status` | Exact status; special value `present` includes active, newly detected, and returned |
| `scanner_id` | Restricts current logical anchor scanner |
| `include_ignored` | Includes operator-ignored logical devices |
| `include_expired` | Includes expired unresolved random identities |
| `include_transient` | Includes unresolved random-address broadcasts; API default is `true`, while the Devices and Location views explicitly request `false` |

Default ordering is newest `last_seen_at` first. Records include identity basis, `visibility_class` (`device_candidate`, `named_broadcast_candidate`, or `transient_broadcast`), presence trackability, direct and inferred device fields, latest capture provenance, signal/radial model, anchor coordinates, and status. A directly captured Local Name can make a random advertiser visible without making it a durable physical identity. Hiding a transient record does not delete its raw observation.

### `GET /api/devices/{device_id}`

Returns:

- current serialized logical device;
- raw observed identities and manufacturer evidence;
- up to 100 recent observations;
- up to 100 location estimates;
- up to 100 events;
- up to 100 identity-correlation records;
- up to 100 GATT enrichment records.

A correlation `proposal` is review evidence only. Apple Continuity proposals include subtype, hashed transition-token, Handoff IV, time, RSSI, GATT model, candidate-count, and score-margin evidence where available. They are never automatically accepted. Only an accepted evidence policy or a manual action can alter logical ownership.

### `GET /api/scanners`

Returns all scanners with location, configuration version, heartbeat/connectivity state, firmware, uptime, and current operational counters. Token hashes and plaintext tokens are excluded.

### `GET /api/events`

Optional filters are `event_type`, `scanner_id`, `device_id`, and `limit`. `limit` is clamped to 1-500. Events are returned newest first.

### `GET /api/settings`

Returns backend policy records with value, description, and update timestamp. These records are an engineering API, not test-console controls. A setting being present does not prove a consumer or scheduled worker exists; see the engineering guide for implemented consumers.

### `GET /api/diagnostics`

Returns server/database status, observation/event/error counts, tracking session/sample counts, identity counts, latest heartbeat counters, and up to 20 recent processing errors. It does not return secrets or full raw request bodies.

### `GET /api/live/events`

Opens a `text/event-stream` connection. Messages notify clients to refresh durable state. The stream is best effort and sends keepalive records; clients must recover by querying normal HTTP endpoints.

## Backend Operator Endpoints

### `PATCH /api/scanners/{scanner_id}`

Accepted fields are display name, enabled state, building, floor, room, zone, latitude, longitude, indoor coordinates, orientation, and maintenance notes. Unknown fields are rejected. A successful patch increments `config_version`.

Coordinates supplied through this endpoint are marked as operator-configured values. Use the live position endpoint for repeated browser fixes; do not poll this configuration endpoint with GPS updates.

### `POST /api/scanners/{scanner_id}/position`

Stores a directly reported current scanner position without changing firmware configuration:

```json
{
  "observed_at": "2026-07-29T08:15:12.481Z",
  "latitude": -6.2261,
  "longitude": 106.8529,
  "accuracy_m": 14.2,
  "source": "browser_geolocation"
}
```

`observed_at`, coordinates, accuracy, and source are required. The only accepted source is `browser_geolocation`. A timestamp more than one minute in the future is rejected with HTTP 400. An update older than or equal to the scanner's stored position timestamp returns HTTP 200 with `position_applied: false` and does not rewind the scanner.

The response is the scanner representation plus `position_applied`. It includes `location_source`, `location_observed_at`, and `location_accuracy_m`. The endpoint does not increment `config_version`, create a BLE observation, alter a logical device, or create a device-location event. A logical-device anchor changes only when a newer accepted BLE observation snapshots the scanner's then-current position.

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
| `409` | Tracking lease/session state conflict or scanner already assigned |
| `422` | JSON/schema validation failure |
| `500` | Unhandled server/database failure |

Scanner clients must retry transport and 5xx failures with backoff and stable identifiers. They must not change batch/observation IDs on retry. Validation failures require a firmware or payload fix; replaying the same invalid document cannot succeed.
