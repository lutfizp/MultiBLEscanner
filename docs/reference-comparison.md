# Reference Comparison

## MetaRadar

Useful ideas adopted:

- Bluetooth SIG Company Identifier lookup from `BluetoothSIG.kt`.
- Manufacturer enrichment from BLE manufacturer data.
- Device category inference from service UUID and device name.
- Clear warning that RSSI distance is approximate.
- Map interpretation: location marker represents scanner location at detection time, not exact Bluetooth-device coordinates.
- Random/private address caution.

Adapted with stricter bounds:

- Active GATT enrichment now attempts one eligible connectable target per scan cycle. It reads standard GAP and Device Information values without forcing pairing, records partial/security/failure states, and does not substitute missing values.

Not adopted:

- Unbounded active GATT deep analysis. It would reduce scan coverage, increase connection activity, and create avoidable heap pressure.
- Android-specific background scan filters. The current scanner is ESP32 firmware, not Android.

## Go Haystack

Useful ideas adopted:

- Apple Company ID `0x004C`.
- Find My payload recognition for registered/unregistered payload type and battery status.
- Treat Find My/AirTag-style payloads as a special beacon category.

Not adopted:

- Apple Find My network integration and Apple ID/Macless Haystack flow. This project is a local BLE scanner/monitoring system, not a Find My network client.
- Beacon key generation/flashing flow. The ESP32 firmware here scans devices; it does not create tracking beacons.

## Current Project Advantage

Compared with the references, this project is stronger for:

- Multi-scanner central backend.
- Scanner registration and token auth.
- Idempotent batch ingestion.
- Bounded RAM buffering and duplicate-safe retry design.
- Historical observation/event storage.
- Backend monitoring APIs across scanners and locations.

The references are stronger for BLE metadata interpretation, so those ideas are used as evidence-scoped enrichment inside the backend. The current firmware queue is volatile and must not be described as durable offline storage.
