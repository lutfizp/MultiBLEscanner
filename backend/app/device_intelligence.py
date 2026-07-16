from __future__ import annotations

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

    data = bytes.fromhex(manufacturer_data)
    if company_id == 0x004C:
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

