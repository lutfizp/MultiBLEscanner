from __future__ import annotations

import argparse
import sys
import time

import serial

from serial_bridge import resolve_serial_port


def main() -> int:
    parser = argparse.ArgumentParser(description="Read raw ESP32 serial output for diagnostics.")
    parser.add_argument("--port", default="auto")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--seconds", type=float, default=10)
    args = parser.parse_args()

    try:
        port = resolve_serial_port(args.port)
    except Exception as exc:  # noqa: BLE001
        print(exc, file=sys.stderr)
        return 1

    print(f"Reading {port} at {args.baud} baud for {args.seconds:g}s...", file=sys.stderr)
    with serial.Serial(port, args.baud, timeout=1) as connection:
        connection.dtr = False
        connection.rts = False
        end = time.time() + args.seconds
        while time.time() < end:
            line = connection.readline()
            if line:
                sys.stdout.buffer.write(line)
                sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
