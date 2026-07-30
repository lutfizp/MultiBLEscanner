"""Map RSSI to a 0..1 closeness value inside a calibrated radius."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .serial_reader import AdvEvent


@dataclass
class DeviceState:
    addr: str
    name: str
    rssi_raw: float
    rssi_smooth: float
    last_seen: float
    hit_count: int = 1
    cid: int | None = None


@dataclass
class ProximityConfig:
    rssi_far: float = -85.0
    rssi_near: float = -45.0
    smooth_alpha: float = 0.35
    timeout_s: float = 2.0
    target_name: Optional[str] = None
    target_addr: Optional[str] = None


@dataclass
class ProximityResult:
    addr: Optional[str]
    name: str
    rssi: Optional[float]
    closeness: float
    volume: float
    tracked: int = 0


@dataclass
class ProximityTracker:
    config: ProximityConfig = field(default_factory=ProximityConfig)
    _devices: dict[str, DeviceState] = field(default_factory=dict)

    def update(self, event: AdvEvent) -> None:
        if self.config.target_addr and event.addr.upper() != self.config.target_addr.upper():
            return
        if self.config.target_name:
            needle = self.config.target_name.lower()
            if needle not in (event.name or "").lower():
                return

        now = time.time()
        prev = self._devices.get(event.addr)
        if prev is None:
            smooth = float(event.rssi)
            hits = 1
        else:
            a = self.config.smooth_alpha
            smooth = a * float(event.rssi) + (1.0 - a) * prev.rssi_smooth
            hits = prev.hit_count + 1

        name = event.name or (prev.name if prev else "")
        cid = event.cid if event.cid is not None else (prev.cid if prev else None)
        self._devices[event.addr] = DeviceState(
            addr=event.addr,
            name=name,
            rssi_raw=float(event.rssi),
            rssi_smooth=smooth,
            last_seen=now,
            hit_count=hits,
            cid=cid,
        )

    def _prune(self, now: float) -> None:
        dead = [
            addr
            for addr, st in self._devices.items()
            if (now - st.last_seen) > self.config.timeout_s
        ]
        for addr in dead:
            del self._devices[addr]

    def list_devices(
        self, *, prune: bool = True, named_first: bool = True
    ) -> list[DeviceState]:
        now = time.time()
        if prune:
            self._prune(now)
        # Named devices first (easier to pick), then strongest RSSI
        def key(d: DeviceState) -> tuple:
            named = 0 if (d.name and d.name.strip()) else 1
            if named_first:
                return (named, -d.rssi_smooth)
            return (0, -d.rssi_smooth)

        return sorted(self._devices.values(), key=key)

    def set_target_addr(self, addr: str | None) -> None:
        self.config.target_addr = addr
        if addr:
            # Drop others so evaluate only sees the pick
            keep = {a: d for a, d in self._devices.items() if a.upper() == addr.upper()}
            self._devices = keep

    def rssi_to_closeness(self, rssi: float) -> float:
        far = self.config.rssi_far
        near = self.config.rssi_near
        if near <= far:
            return 0.0
        x = (rssi - far) / (near - far)
        if x < 0.0:
            return 0.0
        if x > 1.0:
            return 1.0
        return x

    def evaluate(self) -> ProximityResult:
        now = time.time()
        self._prune(now)

        if not self._devices:
            return ProximityResult(
                addr=None, name="", rssi=None, closeness=0.0, volume=0.0, tracked=0
            )

        if self.config.target_addr:
            key = self.config.target_addr.upper()
            best = next((d for d in self._devices.values() if d.addr.upper() == key), None)
            if best is None:
                return ProximityResult(
                    addr=self.config.target_addr,
                    name="",
                    rssi=None,
                    closeness=0.0,
                    volume=0.0,
                    tracked=0,
                )
        else:
            best = max(self._devices.values(), key=lambda d: d.rssi_smooth)

        closeness = self.rssi_to_closeness(best.rssi_smooth)
        return ProximityResult(
            addr=best.addr,
            name=best.name,
            rssi=best.rssi_smooth,
            closeness=closeness,
            volume=closeness,
            tracked=len(self._devices),
        )
