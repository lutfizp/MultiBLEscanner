"""CLI: scan BLE devices, pick one, then RSSI proximity audio."""

from __future__ import annotations

import argparse
import sys
import time

from .audio_engine import AudioEngine
from .labels import display_name
from .proximity import DeviceState, ProximityConfig, ProximityTracker
from .serial_reader import SerialReader, list_candidate_ports


def _pick_port(explicit: str | None) -> str:
    if explicit:
        return explicit
    ports = list_candidate_ports()
    if not ports:
        print(
            "No serial port found. Plug in the nRF52840 Dongle "
            "or pass --port /dev/tty.usbmodemXXXX",
            file=sys.stderr,
        )
        sys.exit(1)
    if len(ports) > 1:
        print("Multiple ports found:")
        for p in ports:
            print(f"  {p}")
        print(f"Using {ports[0]} (override with --port)")
    return ports[0]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="host.cli",
        description="Scan BLE → pilih device → proximity audio (volume naik saat dekat)",
    )
    p.add_argument("--port", help="USB CDC serial port (auto-detect if omitted)")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument(
        "--rssi-far",
        type=float,
        default=-85.0,
        help="RSSI at outer radius edge (volume 0), default -85",
    )
    p.add_argument(
        "--rssi-near",
        type=float,
        default=-45.0,
        help="RSSI at inner radius (volume 1), default -45",
    )
    p.add_argument(
        "--target-name",
        help="Skip picker: track advertisers whose name contains this",
    )
    p.add_argument(
        "--target-addr",
        help="Skip picker: track this MAC (AA:BB:CC:DD:EE:FF)",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="Seconds before a device drops from the scan list (default 5)",
    )
    p.add_argument(
        "--scan-seconds",
        type=float,
        default=8.0,
        help="Scan duration before picker (default 8). Use 0 to wait for Enter only.",
    )
    p.add_argument("--mute", action="store_true", help="UI only, no audio")
    p.add_argument(
        "--named-only",
        action="store_true",
        help="Hanya tampilkan device yang punya Local Name",
    )
    p.add_argument(
        "--min-rssi",
        type=float,
        default=-70.0,
        help="Sembunyikan device lebih lemah dari ini (default -70). Pakai -100 untuk semua.",
    )
    p.add_argument(
        "--hz",
        type=float,
        default=4.0,
        help="Scan list refresh rate (default 4)",
    )
    return p


def _filter_devices(
    devices: list[DeviceState],
    *,
    named_only: bool,
    min_rssi: float,
) -> list[DeviceState]:
    out = [d for d in devices if d.rssi_smooth >= min_rssi]
    if named_only:
        out = [d for d in out if d.name and d.name.strip()]
    return out


def _label(d: DeviceState) -> str:
    return display_name(d.name, d.cid)


def _render_table(devices: list[DeviceState], *, now: float) -> str:
    named = sum(1 for d in devices if d.name and d.name.strip())
    lines = [
        f"named={named}/{len(devices)}   "
        f"~Apple/~Samsung = tebakan pabrik (bukan nama HP). "
        f"Ketik i = identify HP.",
        f"{'#':>3}  {'ADDR':<17}  {'LABEL':<20}  {'RSSI':>7}  {'AGE':>5}  hits",
        "-" * 72,
    ]
    if not devices:
        lines.append(
            "  (kosong — longgarkan --min-rssi, atau nyalakan advertising di HP)"
        )
        return "\n".join(lines)

    for i, d in enumerate(devices, start=1):
        age = max(0.0, now - d.last_seen)
        name = _label(d)[:20]
        lines.append(
            f"{i:>3}  {d.addr:<17}  {name:<20}  {d.rssi_smooth:>6.1f}  {age:>4.1f}s  {d.hit_count}"
        )
    return "\n".join(lines)


def _identify_nearest(tracker: ProximityTracker, *, hold_s: float = 3.0) -> str | None:
    """Ask user to hold phone next to dongle; pick the strongest new/closest RSSI."""
    before = {d.addr: d.rssi_smooth for d in tracker.list_devices()}
    print(
        "\n=== IDENTIFY ===\n"
        f"Dekatkan HP ke dongle sekarang ({hold_s:g}s)…\n"
    )
    time.sleep(hold_s)
    after = tracker.list_devices()
    if not after:
        print("Tidak ada device terdeteksi.")
        return None

    scored: list[tuple[float, DeviceState]] = []
    for d in after:
        prev = before.get(d.addr)
        # Prefer biggest RSSI jump; also favor absolute strength
        jump = (d.rssi_smooth - prev) if prev is not None else 25.0
        score = jump + max(0.0, d.rssi_smooth + 40.0) * 0.15
        scored.append((score, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][1]
    print(
        f"Terdeteksi paling dekat: {best.addr}  {_label(best)}  "
        f"RSSI={best.rssi_smooth:.1f} dBm"
    )
    return best.addr


def _resolve_pick(raw: str, devices: list[DeviceState]) -> str | None:
    text = raw.strip()
    if not text:
        return None
    if text.isdigit():
        idx = int(text)
        if 1 <= idx <= len(devices):
            return devices[idx - 1].addr
        return None
    needle = text.upper().replace("-", ":")
    for d in devices:
        if d.addr.upper() == needle or needle in d.addr.upper():
            return d.addr
    low = text.lower()
    matches = [
        d
        for d in devices
        if low in (d.name or "").lower() or low in _label(d).lower()
    ]
    if len(matches) == 1:
        return matches[0].addr
    if len(matches) > 1:
        print("Beberapa device cocok; pakai nomor saja:")
        for i, d in enumerate(matches, start=1):
            print(f"  {i}. {d.addr}  {_label(d)}")
    return None


def _stdin_ready() -> bool:
    import select

    if not sys.stdin.isatty():
        return False
    try:
        return bool(select.select([sys.stdin], [], [], 0)[0])
    except (OSError, ValueError):
        return False


def _scan_and_pick(
    tracker: ProximityTracker,
    *,
    refresh_hz: float,
    scan_seconds: float,
    named_only: bool,
    min_rssi: float,
) -> str | None:
    """Live scan table, then freeze and ask which device to track."""
    print(
        "\n=== SCAN ===\n"
        "HP biasanya TIDAK broadcast nama Bluetooth.\n"
        "Cara mudah: setelah daftar muncul, ketik  i  lalu dekatkan HP ke dongle.\n"
        f"Scan ~{scan_seconds:g}s (atau Enter untuk pilih sekarang).\n"
        "Ctrl+C batal.\n"
    )

    interval = 1.0 / max(refresh_hz, 1.0)
    started = time.time()

    try:
        while True:
            devices = _filter_devices(
                tracker.list_devices(), named_only=named_only, min_rssi=min_rssi
            )
            now = time.time()
            elapsed = now - started
            sys.stdout.write("\033[2J\033[H")
            print(
                f"Scanning… {elapsed:4.1f}s   devices={len(devices)}   "
                f"[Enter = pilih]  [ketik i = identify HP]"
            )
            print(_render_table(devices, now=now))
            sys.stdout.flush()

            if _stdin_ready():
                sys.stdin.readline()
                break
            if scan_seconds > 0 and elapsed >= scan_seconds:
                break
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nDibatalkan.")
        return None

    devices = _filter_devices(
        tracker.list_devices(), named_only=named_only, min_rssi=min_rssi
    )
    sys.stdout.write("\033[2J\033[H")
    print("=== PILIH DEVICE UNTUK DF / PROXIMITY ===\n")
    print(_render_table(devices, now=time.time()))
    print(
        "\nTips: ketik  i  = identify (dekatkan HP ke dongle).\n"
        "Atau di HP buka nRF Connect → Advertising → isi nama → Start."
    )
    if not devices:
        print("\nTidak ada device (coba --min-rssi -90).")
        return None

    while True:
        try:
            raw = input("\nPilih (# / MAC / nama / i): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nDibatalkan.")
            return None
        if not raw:
            print("Contoh: 2   atau   i")
            continue
        if raw.lower() in ("i", "identify", "id"):
            addr = _identify_nearest(tracker)
            if addr:
                return addr
            continue
        addr = _resolve_pick(raw, devices)
        if addr:
            return addr
        print(f"Tidak valid: {raw!r}. Coba lagi.")


def _track_loop(
    tracker: ProximityTracker,
    *,
    mute: bool,
    hz: float,
    rssi_far: float,
    rssi_near: float,
) -> int:
    audio: AudioEngine | None = None
    print(
        f"\n=== PROXIMITY ===\n"
        f"Target: {tracker.config.target_addr}\n"
        f"Radius: {rssi_far} dBm (jauh/diam) → {rssi_near} dBm (dekat/keras)\n"
        f"Jalan mendekati dongle. Ctrl+C stop.\n"
    )
    try:
        if not mute:
            audio = AudioEngine()
            audio.start()

        interval = 1.0 / max(hz, 1.0)
        while True:
            result = tracker.evaluate()
            if audio:
                audio.set_volume(result.volume)

            if result.rssi is None:
                line = (
                    f"{result.addr or '-'}  (hilang dari scan…)  "
                    f"vol={result.volume * 100:5.1f}%"
                )
            else:
                name = result.name or "-"
                line = (
                    f"{result.addr}  {name[:18]:<18}  "
                    f"RSSI={result.rssi:6.1f} dBm  "
                    f"close={result.closeness * 100:5.1f}%  "
                    f"vol={result.volume * 100:5.1f}%"
                )
            print(f"\r{line:<100}", end="", flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nBye.")
    finally:
        if audio:
            audio.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.rssi_near <= args.rssi_far:
        print("--rssi-near must be greater than --rssi-far (e.g. -45 > -85)", file=sys.stderr)
        return 2

    port = _pick_port(args.port)
    cfg = ProximityConfig(
        rssi_far=args.rssi_far,
        rssi_near=args.rssi_near,
        timeout_s=args.timeout,
        target_name=None,
        target_addr=None,
    )
    tracker = ProximityTracker(config=cfg)

    def on_status(obj: dict) -> None:
        if obj.get("t") == "error":
            print(f"\n[dongle] {obj}", flush=True)

    reader = SerialReader(port=port, baud=args.baud, on_adv=tracker.update, on_status=on_status)

    print(f"Opening {port} …")
    try:
        reader.start()
        time.sleep(0.5)

        if args.target_addr:
            tracker.set_target_addr(args.target_addr)
            tracker.config.timeout_s = max(args.timeout, 2.0)
            return _track_loop(
                tracker,
                mute=args.mute,
                hz=max(args.hz, 10.0),
                rssi_far=args.rssi_far,
                rssi_near=args.rssi_near,
            )

        if args.target_name:
            cfg.target_name = args.target_name
            print(f"Filter nama: *{args.target_name}* — scanning 3s…")
            time.sleep(3.0)
            devices = tracker.list_devices()
            if not devices:
                print("Tidak ada device yang cocok.")
                return 1
            tracker.set_target_addr(devices[0].addr)
            tracker.config.target_name = None
            tracker.config.timeout_s = max(args.timeout, 2.0)
            print(f"Auto-pilih (terkuat): {devices[0].addr}  {devices[0].name or '-'}")
            return _track_loop(
                tracker,
                mute=args.mute,
                hz=max(args.hz, 10.0),
                rssi_far=args.rssi_far,
                rssi_near=args.rssi_near,
            )

        addr = _scan_and_pick(
            tracker,
            refresh_hz=args.hz,
            scan_seconds=args.scan_seconds,
            named_only=args.named_only,
            min_rssi=args.min_rssi,
        )
        if not addr:
            return 1

        chosen = next((d for d in tracker.list_devices() if d.addr == addr), None)
        print(f"\nDipilih: {addr}  {_label(chosen) if chosen else '-'}")
        tracker.set_target_addr(addr)
        tracker.config.timeout_s = max(args.timeout, 2.0)
        return _track_loop(
            tracker,
            mute=args.mute,
            hz=max(args.hz, 10.0),
            rssi_far=args.rssi_far,
            rssi_near=args.rssi_near,
        )
    finally:
        reader.stop()


if __name__ == "__main__":
    raise SystemExit(main())
