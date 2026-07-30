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

## DF Bluetooth

The local `df-bluetooth` reference uses an nRF52840 dongle with Zephyr, not the installed ESP32-D0WD-V3. Its useful interaction flow is scan, select one observed address, keep high-rate RSSI updates for that target, smooth RSSI with an EMA, and drive relative audio feedback.

Ideas adopted:

- Explicit device selection before focused acquisition.
- Active scanning during target search.
- EMA-smoothed RSSI, a bounded zero-to-one signal scale, stale-target handling, and stronger-signal audio feedback.
- Clear separation between the discovery list and the focused target view.

Adapted for this backend:

- Selection starts from a persisted logical device and resolves only its already accepted raw address/address-type pairs.
- Focus samples use leases, scanner assignments, authenticated idempotent batches, dedicated tables, and topic-isolated SSE.
- Normal discovery continues during focus without letting duplicate-enabled callbacks inflate logical-device records.
- Fixed and Walk map overlays show where measurements occurred without inventing direction or changing durable anchors.

Not adopted:

- Automatic strongest-address selection or the reference's nearby-device jump heuristic. Those are convenient picker heuristics, not identity proof.
- Treating an address as permanently equivalent to a physical device.
- The nRF52840/Zephyr firmware and USB format, because the installed hardware and existing serial protocol are different.
- Any direction-finding claim. The reference itself is an RSSI proximity tool; neither its single dongle nor this ESP32 produces Bluetooth AoA bearing.

## Current Project Advantage

Compared with the references, this project is stronger for:

- Multi-scanner central backend.
- Scanner registration and token auth.
- Idempotent batch ingestion.
- Bounded RAM buffering and duplicate-safe retry design.
- Historical observation/event storage.
- Backend monitoring APIs across scanners and locations.

The references are stronger for BLE metadata interpretation, so those ideas are used as evidence-scoped enrichment inside the backend. The current firmware queue is volatile and must not be described as durable offline storage.
