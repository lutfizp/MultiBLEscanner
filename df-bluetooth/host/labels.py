"""Bluetooth company ID → short label (when Local Name is absent)."""

from __future__ import annotations

# Subset of Bluetooth SIG Company Identifiers (little-endian CID in AD)
COMPANY_NAMES: dict[int, str] = {
    0x0006: "Microsoft",
    0x000F: "Broadcom",
    0x004C: "Apple",
    0x0075: "Samsung",
    0x0087: "Garmin",
    0x00D2: "Sony",
    0x00E0: "Google",
    0x0157: "Anhui Huami",
    0x02E5: "Espressif",
    0x0059: "NordicSemi",
    0x000A: "CSR",
    0x0131: "Cypress",
    0x01D7: "Xiaomi",
    0x038F: "Xiaomi",
    0x0171: "Amazon",
    0x0499: "Ruuvi",
    0x0A62: "OPPO",
}


def company_label(cid: int | None) -> str:
    if cid is None:
        return ""
    name = COMPANY_NAMES.get(int(cid))
    if name:
        return f"~{name}"
    return f"~cid:{int(cid):04X}"


def display_name(name: str, cid: int | None = None) -> str:
    if name and name.strip():
        return name.strip()
    label = company_label(cid)
    return label if label else "(no name)"
