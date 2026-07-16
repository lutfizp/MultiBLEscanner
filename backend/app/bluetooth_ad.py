from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any


AD_TYPE_NAMES = {
    0x01: "flags",
    0x02: "incomplete_16bit_service_uuids",
    0x03: "complete_16bit_service_uuids",
    0x04: "incomplete_32bit_service_uuids",
    0x05: "complete_32bit_service_uuids",
    0x06: "incomplete_128bit_service_uuids",
    0x07: "complete_128bit_service_uuids",
    0x08: "shortened_local_name",
    0x09: "complete_local_name",
    0x0A: "tx_power",
    0x12: "peripheral_connection_interval_range",
    0x16: "service_data_16bit",
    0x19: "appearance",
    0x20: "service_data_32bit",
    0x21: "service_data_128bit",
    0x24: "uri",
    0xFF: "manufacturer_specific_data",
}


UUID_LIST_WIDTHS = {
    0x02: 2,
    0x03: 2,
    0x04: 4,
    0x05: 4,
    0x06: 16,
    0x07: 16,
}


SERVICE_DATA_WIDTHS = {
    0x16: 2,
    0x20: 4,
    0x21: 16,
}


@dataclass(frozen=True)
class ParsedAdPayload:
    source: str
    structures: list[dict[str, Any]]
    errors: list[dict[str, Any]]


def uuid_from_little_endian(raw: bytes) -> str:
    width = len(raw)
    if width == 2:
        return f"{int.from_bytes(raw, 'little'):04x}"
    if width == 4:
        return f"{int.from_bytes(raw, 'little'):08x}"
    if width != 16:
        raise ValueError(f"unsupported UUID width: {width}")
    value = raw[::-1].hex()
    return f"{value[:8]}-{value[8:12]}-{value[12:16]}-{value[16:20]}-{value[20:]}"


def _parse_local_name(value: bytes, source: str, offset: int, errors: list[dict[str, Any]]) -> str | None:
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError:
        errors.append(
            {
                "source": source,
                "offset": offset,
                "code": "local_name_not_utf8",
                "message": "Local-name AD structure is not valid UTF-8.",
            }
        )
        return None


def parse_ad_payload(payload_hex: str | None, source: str) -> ParsedAdPayload:
    if not payload_hex:
        return ParsedAdPayload(source=source, structures=[], errors=[])

    payload = bytes.fromhex(payload_hex)
    structures: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    offset = 0
    while offset < len(payload):
        length = payload[offset]
        if length == 0:
            trailing = payload[offset + 1 :]
            if any(trailing):
                errors.append(
                    {
                        "source": source,
                        "offset": offset,
                        "code": "nonzero_data_after_terminator",
                        "message": "Non-zero bytes follow an AD terminator.",
                    }
                )
            break

        end = offset + 1 + length
        if end > len(payload):
            errors.append(
                {
                    "source": source,
                    "offset": offset,
                    "code": "truncated_ad_structure",
                    "message": "AD structure length exceeds the captured payload.",
                    "declared_length": length,
                    "available_length": len(payload) - offset - 1,
                }
            )
            break

        ad_type = payload[offset + 1]
        value = payload[offset + 2 : end]
        structure: dict[str, Any] = {
            "source": source,
            "offset": offset,
            "length": length,
            "type": f"0x{ad_type:02x}",
            "type_name": AD_TYPE_NAMES.get(ad_type, "unknown"),
            "data": value.hex(),
        }

        if ad_type in UUID_LIST_WIDTHS:
            width = UUID_LIST_WIDTHS[ad_type]
            if len(value) % width:
                errors.append(
                    {
                        "source": source,
                        "offset": offset,
                        "code": "invalid_service_uuid_list_length",
                        "message": "Service UUID list length is not divisible by its UUID width.",
                    }
                )
            else:
                structure["service_uuids"] = [
                    uuid_from_little_endian(value[index : index + width])
                    for index in range(0, len(value), width)
                ]
        elif ad_type in {0x08, 0x09}:
            local_name = _parse_local_name(value, source, offset, errors)
            if local_name is not None:
                structure["local_name"] = local_name
                structure["name_completeness"] = "complete" if ad_type == 0x09 else "shortened"
        elif ad_type == 0x0A:
            if len(value) != 1:
                errors.append(
                    {
                        "source": source,
                        "offset": offset,
                        "code": "invalid_tx_power_length",
                        "message": "TX power AD structure must contain exactly one byte.",
                    }
                )
            else:
                structure["tx_power"] = int.from_bytes(value, "little", signed=True)
        elif ad_type == 0x19:
            if len(value) != 2:
                errors.append(
                    {
                        "source": source,
                        "offset": offset,
                        "code": "invalid_appearance_length",
                        "message": "Appearance AD structure must contain exactly two bytes.",
                    }
                )
            else:
                structure["appearance"] = str(int.from_bytes(value, "little"))
        elif ad_type == 0x01:
            if len(value) != 1:
                errors.append(
                    {
                        "source": source,
                        "offset": offset,
                        "code": "invalid_flags_length",
                        "message": "Flags AD structure must contain exactly one byte.",
                    }
                )
            else:
                flags = value[0]
                structure["flags"] = {
                    "raw": value.hex(),
                    "limited_discoverable_mode": bool(flags & 0x01),
                    "general_discoverable_mode": bool(flags & 0x02),
                    "br_edr_not_supported": bool(flags & 0x04),
                    "simultaneous_le_br_edr_controller": bool(flags & 0x08),
                    "simultaneous_le_br_edr_host": bool(flags & 0x10),
                }
        elif ad_type in SERVICE_DATA_WIDTHS:
            width = SERVICE_DATA_WIDTHS[ad_type]
            if len(value) < width:
                errors.append(
                    {
                        "source": source,
                        "offset": offset,
                        "code": "truncated_service_data_uuid",
                        "message": "Service data does not contain its required service UUID.",
                    }
                )
            else:
                structure["service_uuid"] = uuid_from_little_endian(value[:width])
                structure["service_data"] = value[width:].hex()
        elif ad_type == 0xFF:
            if len(value) < 2:
                errors.append(
                    {
                        "source": source,
                        "offset": offset,
                        "code": "truncated_manufacturer_company_id",
                        "message": "Manufacturer data does not contain a Bluetooth SIG company identifier.",
                    }
                )
            else:
                structure["company_id"] = f"0x{int.from_bytes(value[:2], 'little'):04x}"

        structures.append(structure)
        offset = end

    return ParsedAdPayload(source=source, structures=structures, errors=errors)


def _single_value(values: list[Any], field: str, errors: list[dict[str, Any]]) -> Any | None:
    distinct: list[Any] = []
    for value in values:
        if value not in distinct:
            distinct.append(value)
    if not distinct:
        return None
    if len(distinct) == 1:
        return distinct[0]
    errors.append(
        {
            "code": "conflicting_ad_field",
            "field": field,
            "message": f"Multiple distinct {field} values were captured; no canonical value was selected.",
            "values": distinct,
        }
    )
    return None


def parse_advertising_and_scan_response(
    advertising_payload_hex: str | None,
    scan_response_payload_hex: str | None,
) -> dict[str, Any]:
    advertising = parse_ad_payload(advertising_payload_hex, "advertising")
    scan_response = parse_ad_payload(scan_response_payload_hex, "scan_response")
    structures = advertising.structures + scan_response.structures
    errors = advertising.errors + scan_response.errors

    complete_names = [
        structure["local_name"]
        for structure in structures
        if structure.get("name_completeness") == "complete" and "local_name" in structure
    ]
    shortened_names = [
        structure["local_name"]
        for structure in structures
        if structure.get("name_completeness") == "shortened" and "local_name" in structure
    ]
    name = _single_value(complete_names, "complete_local_name", errors)
    if name is None and not complete_names:
        name = _single_value(shortened_names, "shortened_local_name", errors)

    tx_power = _single_value(
        [structure["tx_power"] for structure in structures if "tx_power" in structure],
        "tx_power",
        errors,
    )
    appearance = _single_value(
        [structure["appearance"] for structure in structures if "appearance" in structure],
        "appearance",
        errors,
    )
    manufacturer_data = _single_value(
        [structure["data"] for structure in structures if structure["type"] == "0xff" and "company_id" in structure],
        "manufacturer_data",
        errors,
    )
    flags = _single_value(
        [structure["flags"] for structure in structures if "flags" in structure],
        "advertising_flags",
        errors,
    )

    service_uuids: list[str] = []
    for structure in structures:
        for service_uuid in structure.get("service_uuids", []):
            if service_uuid not in service_uuids:
                service_uuids.append(service_uuid)

    service_data: dict[str, list[str]] = defaultdict(list)
    for structure in structures:
        service_uuid = structure.get("service_uuid")
        if service_uuid is not None and structure.get("service_data") is not None:
            value = structure["service_data"]
            if value not in service_data[service_uuid]:
                service_data[service_uuid].append(value)

    return {
        "parser_version": 1,
        "capture_complete": not errors,
        "advertising_payload_length": len(advertising_payload_hex or "") // 2,
        "scan_response_payload_length": len(scan_response_payload_hex or "") // 2,
        "scan_response_captured": bool(scan_response_payload_hex),
        "structures": structures,
        "errors": errors,
        "fields": {
            "name": name,
            "service_uuids": service_uuids,
            "service_data": dict(service_data),
            "manufacturer_data": manufacturer_data,
            "appearance": appearance,
            "tx_power": tx_power,
            "advertising_flags": flags or {},
        },
    }
