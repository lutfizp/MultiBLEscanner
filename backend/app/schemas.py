from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .processing import normalize_hex


def empty_datetime_to_none(value: Any) -> Any:
    if value == "":
        return None
    return value


class ScannerRegistrationIn(BaseModel):
    hardware_id: str = Field(min_length=3, max_length=160)
    display_name: Optional[str] = Field(default=None, max_length=160)
    firmware_version: Optional[str] = Field(default=None, max_length=80)
    hardware_version: Optional[str] = Field(default=None, max_length=80)
    installation_name: Optional[str] = Field(default=None, max_length=160)


class ScannerRegistrationOut(BaseModel):
    scanner_id: str
    token: str
    config_version: int
    config: dict[str, Any]


class HeartbeatIn(BaseModel):
    message_id: str = Field(min_length=3, max_length=120)
    scanner_time: Optional[datetime] = None
    uptime_seconds: Optional[int] = Field(default=None, ge=0)
    firmware_version: Optional[str] = Field(default=None, max_length=80)
    network_state: dict[str, Any] = Field(default_factory=dict)
    health: dict[str, Any] = Field(default_factory=dict)
    buffer_usage: int = Field(default=0, ge=0)
    pending_observations: int = Field(default=0, ge=0)
    dropped_observations: int = Field(default=0, ge=0)
    config_version: Optional[int] = Field(default=None, ge=0)
    config_status: Optional[str] = Field(default=None, max_length=80)

    model_config = ConfigDict(extra="forbid")

    @field_validator("scanner_time", mode="before")
    @classmethod
    def empty_datetime_is_none(cls, value: Any) -> Any:
        return empty_datetime_to_none(value)


class GATTEnrichmentIn(BaseModel):
    status: Literal["success", "partial", "connection_failed", "service_discovery_failed", "security_required"]
    device_name: Optional[str] = Field(default=None, max_length=240)
    manufacturer_name: Optional[str] = Field(default=None, max_length=240)
    model_number: Optional[str] = Field(default=None, max_length=240)
    serial_number: Optional[str] = Field(default=None, max_length=240)
    firmware_revision: Optional[str] = Field(default=None, max_length=240)
    hardware_revision: Optional[str] = Field(default=None, max_length=240)
    software_revision: Optional[str] = Field(default=None, max_length=240)
    system_id: Optional[str] = None
    pnp_id: Optional[str] = None
    discovered_services: list[str] = Field(default_factory=list, max_length=128)
    characteristic_values: dict[str, str] = Field(default_factory=dict)
    error_code: Optional[str] = Field(default=None, max_length=120)
    attempt_duration_ms: Optional[int] = Field(default=None, ge=0, le=120_000)

    model_config = ConfigDict(extra="forbid")

    @field_validator("system_id", "pnp_id")
    @classmethod
    def validate_binary_hex(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip().lower().replace(" ", "")
        if cleaned.startswith("0x"):
            cleaned = cleaned[2:]
        if len(cleaned) % 2:
            raise ValueError("GATT binary values must contain whole bytes")
        return normalize_hex(cleaned)

    @field_validator("characteristic_values")
    @classmethod
    def validate_characteristic_values(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 64:
            raise ValueError("At most 64 GATT characteristic values may be reported")
        normalized: dict[str, str] = {}
        for uuid, raw_value in value.items():
            key = str(uuid).strip().lower()
            if not key or len(key) > 80:
                raise ValueError("Invalid GATT characteristic UUID")
            cleaned = str(raw_value).strip().lower().replace(" ", "")
            if len(cleaned) > 1024 or len(cleaned) % 2:
                raise ValueError("Invalid GATT characteristic hex value")
            normalized[key] = normalize_hex(cleaned)
        return normalized


class BLEObservationIn(BaseModel):
    observation_id: str = Field(min_length=3, max_length=120)
    observed_at: Optional[datetime] = None
    scanner_time: Optional[datetime] = None
    time_source: Optional[Literal["usb_host_synchronized", "ntp_synchronized", "unsynchronized"]] = None
    boot_id: Optional[str] = Field(default=None, max_length=160)
    monotonic_ms: Optional[int] = Field(default=None, ge=0)
    scan_cycle: Optional[int] = Field(default=None, ge=0)
    clock_sync_age_ms: Optional[int] = Field(default=None, ge=0, le=86_400_000)
    address: Optional[str] = Field(default=None, max_length=80)
    address_type: Optional[str] = Field(default=None, max_length=80)
    advertised_name: Optional[str] = Field(default=None, max_length=240)
    local_name: Optional[str] = Field(default=None, max_length=240)
    rssi: int = Field(ge=-127, le=20)
    tx_power: Optional[int] = Field(default=None, ge=-127, le=40)
    estimated_distance_m: Optional[float] = Field(default=None, ge=0, le=1000)
    advertising_type: Optional[str] = Field(default=None, max_length=80)
    service_uuids: list[str] = Field(default_factory=list)
    service_data: dict[str, Any] = Field(default_factory=dict)
    manufacturer_data: Optional[str] = None
    appearance: Optional[str] = Field(default=None, max_length=80)
    advertising_flags: dict[str, Any] = Field(default_factory=dict)
    connectable: Optional[bool] = None
    raw_advertising_payload: Optional[str] = None
    raw_scan_response_payload: Optional[str] = None
    packet_length: Optional[int] = Field(default=None, ge=0, le=255)
    advertising_packet_length: Optional[int] = Field(default=None, ge=0, le=255)
    scan_response_packet_length: Optional[int] = Field(default=None, ge=0, le=255)
    payload_layout_version: Optional[int] = Field(default=None, ge=1, le=10)
    device_category: Optional[str] = Field(default=None, max_length=120)
    gatt_enrichment: Optional[GATTEnrichmentIn] = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("observed_at", "scanner_time", mode="before")
    @classmethod
    def empty_datetime_is_none(cls, value: Any) -> Any:
        return empty_datetime_to_none(value)

    @field_validator("manufacturer_data", "raw_advertising_payload", "raw_scan_response_payload")
    @classmethod
    def validate_hex(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip().lower().replace(" ", "")
        if cleaned.startswith("0x"):
            cleaned = cleaned[2:]
        if cleaned and len(cleaned) % 2:
            raise ValueError("Bluetooth payload hex must contain whole bytes")
        return normalize_hex(cleaned)

    @model_validator(mode="after")
    def validate_capture_metadata(self) -> "BLEObservationIn":
        if self.time_source == "usb_host_synchronized":
            # Firmware sends the same synchronized timestamp in both fields.
            # If a legacy/overflowed frame omitted observed_at but preserved
            # scanner_time, retain the direct timestamp rather than rejecting
            # the complete batch at transport validation.
            if self.observed_at is None and self.scanner_time is not None:
                self.observed_at = self.scanner_time
            if self.observed_at is None or self.boot_id is None or self.monotonic_ms is None:
                raise ValueError("USB-synchronized observations require observed_at, boot_id, and monotonic_ms")
        if self.payload_layout_version == 2:
            captured_length = len(self.raw_advertising_payload or "") // 2
            captured_length += len(self.raw_scan_response_payload or "") // 2
            if self.advertising_packet_length is not None and self.advertising_packet_length != len(self.raw_advertising_payload or "") // 2:
                raise ValueError("advertising_packet_length does not match raw_advertising_payload")
            if self.scan_response_packet_length is not None and self.scan_response_packet_length != len(self.raw_scan_response_payload or "") // 2:
                raise ValueError("scan_response_packet_length does not match raw_scan_response_payload")
            if self.packet_length is not None and self.packet_length != captured_length:
                raise ValueError("packet_length does not match separated advertising and scan-response payloads")
        return self


class ObservationBatchIn(BaseModel):
    batch_id: str = Field(min_length=3, max_length=120)
    sent_at: Optional[datetime] = None
    scanner_time: Optional[datetime] = None
    time_source: Optional[Literal["usb_host_synchronized", "ntp_synchronized", "unsynchronized"]] = None
    boot_id: Optional[str] = Field(default=None, max_length=160)
    batch_sequence: Optional[int] = Field(default=None, ge=0)
    clock_sync_age_ms: Optional[int] = Field(default=None, ge=0, le=86_400_000)
    firmware_version: Optional[str] = Field(default=None, max_length=80)
    scanner_uptime_seconds: Optional[int] = Field(default=None, ge=0)
    network_state: dict[str, Any] = Field(default_factory=dict)
    dropped_observations: int = Field(default=0, ge=0)
    observations: list[BLEObservationIn] = Field(min_length=1, max_length=500)

    model_config = ConfigDict(extra="forbid")

    @field_validator("sent_at", "scanner_time", mode="before")
    @classmethod
    def empty_datetime_is_none(cls, value: Any) -> Any:
        return empty_datetime_to_none(value)

    @model_validator(mode="after")
    def validate_time_metadata(self) -> "ObservationBatchIn":
        if self.time_source == "usb_host_synchronized" and (self.sent_at is None or self.boot_id is None):
            raise ValueError("USB-synchronized batches require sent_at and boot_id")
        return self


class ScannerPatchIn(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=160)
    enabled: Optional[bool] = None
    building: Optional[str] = Field(default=None, max_length=120)
    floor: Optional[str] = Field(default=None, max_length=80)
    room: Optional[str] = Field(default=None, max_length=120)
    zone: Optional[str] = Field(default=None, max_length=120)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    indoor_x: Optional[float] = None
    indoor_y: Optional[float] = None
    orientation_deg: Optional[float] = None
    maintenance_notes: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SettingsPatchIn(BaseModel):
    values: dict[str, Any]


class ManualCorrelationIn(BaseModel):
    source_logical_device_id: str
    target_logical_device_id: Optional[str] = None
    observed_identity_id: Optional[str] = None
    action: str = Field(pattern="^(merge|split|mark_known|mark_ignored|unignore)$")
    reason: Optional[str] = None
