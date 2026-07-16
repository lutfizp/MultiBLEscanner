from __future__ import annotations

import argparse
import sys
import time

import serial

from serial_bridge import resolve_serial_port


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect raw output from the connected ESP32 scanner.")
    parser.add_argument("--port", default="auto", help="Serial port or 'auto' (default).")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--seconds", type=float, default=15)
    args = parser.parse_args()

    try:
        port = resolve_serial_port(args.port)
    except Exception as exc:  # noqa: BLE001
        print(f"ESP32 serial port unavailable: {exc}", file=sys.stderr)
        return 1

    print(f"Reading {port} at {args.baud} baud for {args.seconds:g}s...", file=sys.stderr)
    try:
        with serial.Serial(port, args.baud, timeout=1) as connection:
            connection.dtr = False
            connection.rts = False
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                raw = connection.readline()
                if raw:
                    sys.stdout.buffer.write(raw)
                    sys.stdout.flush()
    except serial.SerialException as exc:
        print(f"Unable to open {port}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
