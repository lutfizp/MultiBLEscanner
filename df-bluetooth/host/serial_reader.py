"""Read JSON-line advertisement events from the nRF52840 dongle."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import serial
from serial.tools import list_ports


@dataclass
class AdvEvent:
    addr: str
    addr_type: str
    rssi: int
    name: str
    at_ms: int
    cid: int | None = None
    host_ts: float = field(default_factory=time.time)


EventCallback = Callable[[AdvEvent], None]
StatusCallback = Callable[[dict], None]


def list_candidate_ports() -> list[str]:
    """Return likely Nordic USB CDC ports (macOS/Linux/Windows)."""
    ports: list[str] = []
    for p in list_ports.comports():
        desc = f"{p.description} {p.manufacturer or ''} {p.product or ''}".lower()
        if any(
            key in desc
            for key in (
                "nordic",
                "zephyr",
                "nrf",
                "cdc",
                "usb serial",
                "df bluetooth",
            )
        ):
            ports.append(p.device)
        elif "usbmodem" in p.device or "ttyACM" in p.device or "COM" in p.device:
            ports.append(p.device)
    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for port in ports:
        if port not in seen:
            seen.add(port)
            out.append(port)
    return out


class SerialReader:
    def __init__(
        self,
        port: str,
        baud: int = 115200,
        on_adv: Optional[EventCallback] = None,
        on_status: Optional[StatusCallback] = None,
    ) -> None:
        self.port = port
        self.baud = baud
        self.on_adv = on_adv
        self.on_status = on_status
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._ser: Optional[serial.Serial] = None

    def start(self) -> None:
        self._ser = serial.Serial(self.port, self.baud, timeout=0.2)
        # Discard boot noise
        time.sleep(0.3)
        self._ser.reset_input_buffer()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="serial-reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._ser and self._ser.is_open:
            self._ser.close()
        self._ser = None

    def _run(self) -> None:
        assert self._ser is not None
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = self._ser.read(256)
            except serial.SerialException:
                break
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self._handle_line(line)

    def _handle_line(self, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="ignore").strip()
        if not text or not text.startswith("{"):
            return
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return

        kind = obj.get("t")
        if kind == "adv" and self.on_adv:
            try:
                event = AdvEvent(
                    addr=str(obj.get("addr", "")),
                    addr_type=str(obj.get("type", "")),
                    rssi=int(obj["rssi"]),
                    name=str(obj.get("name") or ""),
                    at_ms=int(obj.get("at") or 0),
                    cid=int(obj["cid"]) if obj.get("cid") is not None else None,
                )
            except (KeyError, TypeError, ValueError):
                return
            self.on_adv(event)
        elif kind in ("status", "error") and self.on_status:
            self.on_status(obj)
