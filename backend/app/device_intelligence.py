from __future__ import annotations

import hashlib
from typing import Any

from .bluetooth_sig import (
    company_identifier_from_manufacturer_data,
    company_identifier_hex,
    company_name_from_manufacturer_data,
)


SERVICE_UUID_CATEGORY = {
    "fe2c": "beacon_airtag_find_my",
    "feed": "beacon_tile_tracker",
    "fd50": "beacon_samsung_smarttag_or_ibeacon",
    "181a": "environmental_beacon",
    "fe9a": "beacon",
    "1843": "audio_video",
    "1858": "gaming_audio",
    "180f": "battery_powered_device",
    "1844": "audio_video",
    "180d": "health_heart_rate",
    "180c": "health_cycling_sensor",
    "1816": "health_cycling_power",
    "181f": "health_glucose",
    "1810": "health_blood_pressure",
    "1809": "health_thermometer",
    "181c": "health_pulse_oximeter",
    "1822": "health_pulse_oximeter",
    "1812": "peripheral_hid",
    "1824": "peripheral_controller",
    "183e": "health_activity_monitor",
    "1840": "health_sensor",
    "1851": "media_control",
    "1853": "common_audio",
}


FLIPPER_ZERO_SERIAL_VARIANTS = {
    "3081": "black",
    "3082": "white",
    "3083": "transparent",
}

FLIPPER_ZERO_GATT_SERIAL_SERVICE = "8fe5b3d5-2e7f-4a98-2a48-7acc60fe0000"
FLIPPER_ZERO_GATT_MANUFACTURER = "flipper devices inc"
FLIPPER_ZERO_PUBLIC_ADDRESS_PREFIXES = ("80:e1:26", "80:e1:27")


NAME_CATEGORY = {
    "buds": "audio_headphones",
    "airpods": "audio_headphones",
    "headphone": "audio_headphones",
    "headset": "audio_headset",
    "speaker": "audio_speaker",
    "soundlink": "audio_portable",
    "stanmore": "audio_speaker",
    "monitor ii": "audio_headphones",
    "phone": "phone",
    "pixel 7": "phone",
    "pixel 8": "phone",
    "pixel 9": "phone",
    "iphone": "phone",
    "ipad": "tablet",
    "watch": "wearable_watch",
    "airtag": "beacon_airtag_find_my",
    "ibeacon": "beacon_ibeacon",
    "smart tag": "beacon_smart_tag",
    "smarttag": "beacon_smart_tag",
    "tile": "beacon_tile_tracker",
    "xbox": "game_controller",
    "keyboard": "peripheral_keyboard",
    "mouse": "peripheral_mouse",
    "macbook": "computer_laptop",
    "imac": "computer_desktop",
    "macmini": "computer_desktop",
    "meta quest": "wearable_glasses",
    " tv": "audio_video_display",
    "tv ": "audio_video_display",
}


FIND_MY_BATTERY_STATUS = {
    0x10: "full",
    0x40: "medium",
    0x80: "low",
    0xC0: "critical",
}

APPLE_CONTINUITY_MESSAGE_NAMES = {
    0x03: "airprint",
    0x05: "airdrop",
    0x06: "homekit",
    0x07: "proximity_pairing",
    0x08: "hey_siri",
    0x09: "airplay",
    0x0B: "magic_switch",
    0x0C: "handoff",
    0x0D: "tethering_target_presence",
    0x0E: "tethering_source_presence",
    0x0F: "nearby_action",
    0x10: "nearby_info",
    0x12: "find_my",
}


def _evidence_hash(scope: str, value: bytes) -> str:
    return hashlib.sha256(scope.encode("ascii") + b":" + value).hexdigest()


def parse_apple_continuity_messages(manufacturer_bytes: bytes) -> list[dict[str, Any]]:
    """Parse Apple company data as the published one-or-more TLV layout.

    Raw bytes remain on the observation. Potentially persistent fields are
    represented as scoped hashes so API responses do not create a second copy
    of broadcast account/device tokens.
    """
    if len(manufacturer_bytes) < 4 or manufacturer_bytes[:2] != b"\x4c\x00":
        return []

    messages: list[dict[str, Any]] = []
    offset = 2
    while offset + 2 <= len(manufacturer_bytes):
        message_type = manufacturer_bytes[offset]
        declared_length = manufacturer_bytes[offset + 1]
        offset += 2
        available_length = min(declared_length, len(manufacturer_bytes) - offset)
        data = manufacturer_bytes[offset : offset + available_length]
        complete = available_length == declared_length
        message: dict[str, Any] = {
            "type": f"0x{message_type:02x}",
            "name": APPLE_CONTINUITY_MESSAGE_NAMES.get(message_type, "unknown"),
            "declared_length": declared_length,
            "captured_length": available_length,
            "complete": complete,
            "payload_hash": _evidence_hash(f"apple-continuity-{message_type:02x}", data),
        }

        if complete and message_type == 0x10 and declared_length == 5:
            message.update(
                {
                    "activity_level": data[0],
                    "information": data[1],
                    "authentication_tag_hash": _evidence_hash("apple-nearby-info-auth-tag", data[2:5]),
                    "correlation_role": "short_lived_address_transition_evidence",
                }
            )
        elif complete and message_type == 0x0C and declared_length == 14:
            message.update(
                {
                    "version": data[0],
                    "iv": int.from_bytes(data[1:3], "big"),
                    "iv_hex": data[1:3].hex(),
                    "authentication_tag_hash": _evidence_hash("apple-handoff-auth-tag", data[3:4]),
                    "encrypted_payload_hash": _evidence_hash("apple-handoff-encrypted-payload", data[4:14]),
                    "correlation_role": "monotonic_transition_evidence",
                }
            )
        elif complete and message_type == 0x0D and declared_length == 4:
            message.update(
                {
                    "identifier_hash": _evidence_hash("apple-tethering-target-identifier", data),
                    "correlation_role": "rotating_account_identifier_evidence",
                }
            )
        elif complete and message_type == 0x0B and declared_length >= 2:
            message.update(
                {
                    "data_hash": _evidence_hash("apple-magic-switch-data", data[:2]),
                    "correlation_role": "protocol_data_transition_evidence",
                }
            )
        elif complete and message_type == 0x0F and declared_length >= 5:
            message.update(
                {
                    "action_flags": data[0],
                    "action_type": data[1],
                    "authentication_tag_hash": _evidence_hash("apple-nearby-action-auth-tag", data[2:5]),
                    "correlation_role": "short_lived_address_transition_evidence",
                }
            )
        elif complete and message_type == 0x07 and declared_length >= 2:
            message.update(
                {
                    "protocol_version": data[0],
                    "device_model_code": f"0x{data[1]:02x}",
                }
            )
        elif complete and message_type == 0x09 and declared_length >= 2:
            message.update(
                {
                    "flags": data[0],
                    "configuration_seed": data[1],
                    "network_address_present": declared_length >= 6,
                }
            )

        messages.append(message)
        offset += available_length
        if not complete:
            break
    return messages


def short_uuid(uuid_value: str) -> str:
    value = uuid_value.strip().lower()
    if len(value) == 4:
        return value
    if value.startswith("0x") and len(value) == 6:
        return value[2:]
    prefix = "0000"
    suffix = "-0000-1000-8000-00805f9b34fb"
    if value.startswith(prefix) and value.endswith(suffix) and len(value) >= 8:
        return value[4:8]
    return value


def _normalized_manufacturer_name(value: str | None) -> str:
    return (value or "").strip().casefold().rstrip(".")


def classify_flipper_zero(
    *,
    service_uuids: list[str] | None = None,
    capture_verified: bool = False,
    name: str | None = None,
    address: str | None = None,
    address_type: str | None = None,
    tx_power: int | None = None,
    advertising_type: str | None = None,
    connectable: bool | None = None,
    advertising_flags: dict[str, Any] | None = None,
    gatt_manufacturer_name: str | None = None,
    gatt_services: list[str] | None = None,
    observation_count: int | None = None,
) -> dict[str, Any] | None:

    advertised_services = {short_uuid(value) for value in service_uuids or []}
    matched_serial_uuid = next(
        (value for value in FLIPPER_ZERO_SERIAL_VARIANTS if value in advertised_services),
        None,
    )
    normalized_gatt_services = {
        value.strip().casefold()
        for value in gatt_services or []
        if isinstance(value, str)
    }
    gatt_verified = (
        _normalized_manufacturer_name(gatt_manufacturer_name)
        == FLIPPER_ZERO_GATT_MANUFACTURER
        and FLIPPER_ZERO_GATT_SERIAL_SERVICE in normalized_gatt_services
    )
    advertisement_verified = capture_verified and matched_serial_uuid is not None
    if not advertisement_verified and not gatt_verified:
        return None

    evidence: list[dict[str, Any]] = []
    if advertisement_verified:
        evidence.append(
            {
                "type": "official_serial_service_uuid",
                "value": f"0x{matched_serial_uuid}",
                "source": "verified_raw_advertising",
            }
        )
    if gatt_verified:
        evidence.extend(
            [
                {
                    "type": "gatt_manufacturer_name",
                    "value": gatt_manufacturer_name,
                    "source": "ble_gatt",
                },
                {
                    "type": "official_gatt_serial_service",
                    "value": FLIPPER_ZERO_GATT_SERIAL_SERVICE,
                    "source": "ble_gatt",
                },
            ]
        )

    flags = advertising_flags or {}
    if capture_verified and tx_power == 0:
        evidence.append(
            {
                "type": "advertised_tx_power",
                "value": "0 dBm",
                "source": "verified_raw_advertising",
            }
        )
    if (
        capture_verified
        and flags.get("general_discoverable_mode")
        and flags.get("br_edr_not_supported")
    ):
        evidence.append(
            {
                "type": "gap_flags",
                "value": "0x06",
                "source": "verified_raw_advertising",
            }
        )
    if (
        capture_verified
        and connectable is True
        and (advertising_type or "").strip().casefold() == "adv_ind"
    ):
        evidence.append(
            {
                "type": "advertising_profile",
                "value": "connectable ADV_IND",
                "source": "radio_metadata",
            }
        )
    if capture_verified and (name or "").strip().casefold().startswith("flipper"):
        evidence.append(
            {
                "type": "advertised_name",
                "value": name,
                "source": "verified_raw_advertising",
            }
        )

    normalized_address = (address or "").strip().casefold()
    normalized_address_type = (address_type or "").strip().casefold()
    if (
        normalized_address_type == "public"
        and normalized_address.startswith(FLIPPER_ZERO_PUBLIC_ADDRESS_PREFIXES)
    ):
        evidence.append(
            {
                "type": "public_address_pattern",
                "value": normalized_address,
                "source": "radio_metadata",
            }
        )
    if observation_count is not None and observation_count > 1:
        evidence.append(
            {
                "type": "repeated_observations",
                "value": observation_count,
                "source": "backend_history",
            }
        )

    variant = (
        FLIPPER_ZERO_SERIAL_VARIANTS.get(matched_serial_uuid)
        if matched_serial_uuid
        else None
    )
    return {
        "product_class": "flipper_zero",
        "label": "Confirmed Flipper Zero",
        "variant": variant,
        "profile": "serial_rpc",
        "confidence_tier": "confirmed",
        "rule_id": "flipper_zero_official_ble_v1",
        "verification_scope": (
            "passive_advertisement_and_gatt"
            if advertisement_verified and gatt_verified
            else "passive_advertisement_fingerprint"
            if advertisement_verified
            else "active_gatt_fingerprint"
        ),
        "spoofable": True,
        "evidence": evidence,
    }


def infer_device_category(name: str | None, service_uuids: list[str] | None, manufacturer_data: str | None = None) -> str | None:
    manufacturer_profile = analyze_manufacturer_data(manufacturer_data)
    if manufacturer_profile.get("find_my"):
        return "beacon_airtag_find_my"
    if manufacturer_profile.get("airdrop"):
        return "apple_nearby_airdrop"

    for service_uuid in service_uuids or []:
        category = SERVICE_UUID_CATEGORY.get(short_uuid(service_uuid))
        if category:
            return category

    lowered = (name or "").strip().lower()
    if lowered:
        for fragment, category in NAME_CATEGORY.items():
            if fragment in lowered:
                return category
    return None


def analyze_manufacturer_data(manufacturer_data: str | None) -> dict[str, Any]:
    company_id = company_identifier_from_manufacturer_data(manufacturer_data)
    company_name = company_name_from_manufacturer_data(manufacturer_data)
    profile: dict[str, Any] = {
        "company_id": company_identifier_hex(manufacturer_data),
        "company_name": company_name,
    }
    if not manufacturer_data:
        return profile

    try:
        data = bytes.fromhex(manufacturer_data)
    except ValueError:
        profile["parse_error"] = "invalid_hex"
        return profile
    if company_id == 0x004C:
        continuity_messages = parse_apple_continuity_messages(data)
        if continuity_messages:
            profile["continuity_messages"] = continuity_messages
            profile["continuity_subtypes"] = sorted(
                {message["name"] for message in continuity_messages}
            )
        find_my = parse_find_my_payload(data)
        if find_my:
            profile["find_my"] = find_my
        airdrop = parse_airdrop_payload(data)
        if airdrop:
            profile["airdrop"] = airdrop
    return profile


def parse_find_my_payload(manufacturer_bytes: bytes) -> dict[str, Any] | None:
    if len(manufacturer_bytes) < 27:
        return None
    payload = manufacturer_bytes[2:]
    payload_type = payload[0]
    if payload_type not in {0x07, 0x12}:
        return None
    if payload_type == 0x12 and (len(payload) < 25 or payload[1] != 0x19):
        return None
    status = payload[2] if len(payload) > 2 else None
    public_key = None
    if payload_type == 0x12 and len(payload) >= 25:
        public_key = payload[3:25].hex()
    return {
        "payload_type": "registered" if payload_type == 0x12 else "unregistered",
        "battery_status": FIND_MY_BATTERY_STATUS.get(status, "unknown") if status is not None else "unknown",
        "status_byte": f"0x{status:02X}" if status is not None else None,
        "public_key": public_key,
    }


def parse_airdrop_payload(manufacturer_bytes: bytes) -> dict[str, Any] | None:
    if len(manufacturer_bytes) != 22 or manufacturer_bytes[2] != 0x05:
        return None
    payload = manufacturer_bytes[3:22]
    if len(payload) != 19:
        return None
    contacts = []
    for index in range(10, 18, 2):
        contacts.append(f"0x{((payload[index] << 8) | payload[index + 1]):04X}")
    return {
        "contact_hash_prefixes": contacts,
        "note": "Apple Nearby/AirDrop-style payload; not a stable physical-device identity.",
    }
