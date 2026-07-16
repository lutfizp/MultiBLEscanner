from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


@lru_cache
def company_identifiers() -> dict[int, str]:
    path = Path(__file__).resolve().parent / "data" / "bluetooth_sig_companies.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {int(key, 16): value for key, value in raw.items()}


def company_identifier_from_manufacturer_data(manufacturer_data: str | None) -> int | None:
    if not manufacturer_data:
        return None
    cleaned = manufacturer_data.strip().lower().replace(" ", "")
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    if len(cleaned) < 4:
        return None
    try:
        first_byte = int(cleaned[0:2], 16)
        second_byte = int(cleaned[2:4], 16)
    except ValueError:
        return None
    return first_byte | (second_byte << 8)


def company_name_from_manufacturer_data(manufacturer_data: str | None) -> str | None:
    company_id = company_identifier_from_manufacturer_data(manufacturer_data)
    if company_id is None:
        return None
    return company_identifiers().get(company_id)


def company_identifier_hex(manufacturer_data: str | None) -> str | None:
    company_id = company_identifier_from_manufacturer_data(manufacturer_data)
    if company_id is None:
        return None
    return f"0x{company_id:04X}"

