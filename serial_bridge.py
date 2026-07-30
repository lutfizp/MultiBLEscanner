from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover
    serial = None
    list_ports = None


ROOT = Path(__file__).resolve().parent
BRIDGE_START = "|||BRIDGE_START|||"
BRIDGE_END = "|||BRIDGE_END|||"
TIME_SYNC_PREFIX = "@@BT_SCANNER_TIME@@"
BRIDGE_ACK_PREFIX = "@@BT_SCANNER_ACK@@"
BRIDGE_CONFIG_PREFIX = "@@BT_SCANNER_CONFIG@@"
MIN_ESP32_PORT_SCORE = 40
DEFAULT_BACKEND_TIMEOUT_SECONDS = 8.0


class SerialLineReader:
    """Reassemble newline-delimited firmware lines across partial serial reads."""

    def __init__(self) -> None:
        self._pending = bytearray()

    def feed(self, chunk: bytes) -> list[str | None]:
        if not chunk:
            return []
        self._pending.extend(chunk)
        lines: list[str | None] = []
        while True:
            newline_index = self._pending.find(b"\n")
            if newline_index < 0:
                break
            raw_line = bytes(self._pending[:newline_index])
            del self._pending[: newline_index + 1]
            try:
                lines.append(raw_line.decode("utf-8").rstrip("\r"))
            except UnicodeDecodeError:
                lines.append(None)
        return lines


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def truthy(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def serial_port_score(port: object) -> int:
    device = str(getattr(port, "device", "") or "")
    description = str(getattr(port, "description", "") or "")
    manufacturer = str(getattr(port, "manufacturer", "") or "")
    haystack = f"{device} {description} {manufacturer}".lower()
    score = 0
    if "bluetooth" in haystack:
        score -= 100
    if "usbserial" in haystack:
        score += 50
    if "usbmodem" in haystack:
        score += 45
    if "wchusbserial" in haystack or "ch340" in haystack:
        score += 40
    if "cp210" in haystack or "silicon labs" in haystack:
        score += 40
    if "espressif" in haystack or "esp32" in haystack:
        score += 60
    if device.startswith("/dev/cu."):
        score += 10
    return score


def resolve_serial_port(preferred: str) -> str:
    if preferred and preferred != "auto":
        return preferred
    if list_ports is None:
        raise RuntimeError("pyserial is not installed. Install requirements first.")

    ports = list(list_ports.comports())
    candidates = sorted(ports, key=serial_port_score, reverse=True)
    if candidates and serial_port_score(candidates[0]) >= MIN_ESP32_PORT_SCORE:
        return str(candidates[0].device)

    visible = ", ".join(
        f"{port.device} ({getattr(port, 'description', '') or 'no description'})"
        for port in ports
    ) or "none"
    raise RuntimeError(f"No ESP32-like serial port found. Visible ports: {visible}")


def build_backend_ssl_context(base_url: str, ca_file: str | None) -> ssl.SSLContext | None:
    if not base_url.lower().startswith("https://"):
        return None
    if ca_file:
        ca_path = Path(ca_file).expanduser()
        if not ca_path.is_file():
            raise RuntimeError(f"Backend CA file does not exist: {ca_path}")
        return ssl.create_default_context(cafile=str(ca_path))
    return ssl.create_default_context()


def send_to_backend(
    method: str,
    path: str,
    body: str,
    base_url: str,
    token: str,
    timeout: float,
    ssl_context: ssl.SSLContext | None = None,
) -> tuple[int, str]:
    method = method.upper()
    if not path.startswith("/"):
        path = "/" + path
    url = base_url.rstrip("/") + path
    data = None if method == "GET" else body.encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout, context=ssl_context) as response:
        response_body = response.read().decode("utf-8", errors="replace")
        return response.status, response_body


def forward_frame(
    lines: list[str],
    base_url: str,
    token: str,
    timeout: float,
    ssl_context: ssl.SSLContext | None = None,
) -> tuple[int, str]:
    if len(lines) < 2:
        return 0, ""

    method = lines[0].strip().upper()
    path = lines[1].strip()
    body = "\n".join(lines[2:])
    if not method or not path or not method.isascii() or not path.isascii():
        print("[serial] Dropping malformed frame before HTTP: method/path is not valid ASCII.", flush=True)
        return 400, ""
    if method not in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
        print(f"[serial] Dropping malformed frame before HTTP: unsupported method {method!r}.", flush=True)
        return 400, ""

    parsed_body = None
    if body:
        try:
            parsed_body = json.loads(body)
        except json.JSONDecodeError as exc:
            context_start = max(0, exc.pos - 48)
            context_end = min(len(body), exc.pos + 48)
            context = body[context_start:context_end]
            print(
                f"[serial] Dropping malformed JSON frame before HTTP: offset={exc.pos} "
                f"error={exc.msg!r} context={context!r}",
                flush=True,
            )
            # A non-2xx acknowledgement keeps the ESP32 batch in its bounded
            # retry queue without sending an invalid document to FastAPI.
            return 400, ""

    if method == "POST" and path.endswith("/observations/batch"):
        valid_batch = (
            isinstance(parsed_body, dict)
            and isinstance(parsed_body.get("batch_id"), str)
            and bool(parsed_body["batch_id"].strip())
            and isinstance(parsed_body.get("observations"), list)
            and bool(parsed_body["observations"])
        )
        if not valid_batch:
            print(
                "[serial] Dropping invalid observation batch frame before HTTP: "
                "batch_id and at least one observation are required.",
                flush=True,
            )
            return 400, ""
    if method == "POST" and path.endswith("/tracking-samples/batch"):
        valid_tracking_batch = (
            isinstance(parsed_body, dict)
            and isinstance(parsed_body.get("batch_id"), str)
            and bool(parsed_body["batch_id"].strip())
            and isinstance(parsed_body.get("session_id"), str)
            and bool(parsed_body["session_id"].strip())
            and isinstance(parsed_body.get("samples"), list)
            and bool(parsed_body["samples"])
        )
        if not valid_tracking_batch:
            print(
                "[serial] Dropping invalid tracking batch frame before HTTP: "
                "batch_id, session_id, and at least one sample are required.",
                flush=True,
            )
            return 400, ""

    try:
        status, response_body = send_to_backend(
            method,
            path,
            body,
            base_url,
            token,
            timeout,
            ssl_context,
        )
        print(f"[serial] Forwarded {method} {path} ({len(body)} bytes) -> HTTP {status}", flush=True)
        return status, response_body
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        print(f"[serial] Backend rejected {method} {path}: HTTP {exc.code} {error_body}", flush=True)
        return exc.code, ""
    except Exception as exc:  # noqa: BLE001
        print(f"[serial] Backend unavailable for {method} {path}: {exc}", flush=True)
        return 0, ""


def send_time_sync(connection: serial.Serial) -> None:
    epoch_ms = time.time_ns() // 1_000_000
    message = f"{TIME_SYNC_PREFIX}{epoch_ms}\n".encode("ascii")
    connection.write(message)
    connection.flush()


def send_bridge_response(connection: serial.Serial, status: int, response_body: str, path: str) -> None:
    if path.endswith("/config") and 200 <= status < 300 and response_body:
        connection.write(f"{BRIDGE_CONFIG_PREFIX}{response_body}\n".encode("utf-8"))
    connection.write(f"{BRIDGE_ACK_PREFIX}{status}\n".encode("ascii"))
    connection.flush()


def run_bridge(args: argparse.Namespace) -> int:
    if serial is None:
        print("pyserial is required. Install it with: python3 -m pip install -r requirements.txt", flush=True)
        return 2

    backend_ssl_context = build_backend_ssl_context(args.base_url, args.ca_file)
    while True:
        try:
            port = resolve_serial_port(args.port)
            print(f"[serial] Opening ESP32 serial port {port} at {args.baud} baud.", flush=True)
            with serial.Serial(port, args.baud, timeout=1) as connection:
                connection.dtr = False
                connection.rts = False
                send_time_sync(connection)
                last_time_sync = time.monotonic()
                print("[serial] ESP32 serial bridge connected. Waiting for BLE scan frames.", flush=True)
                buffer: list[str] = []
                in_bridge_frame = False
                frame_has_invalid_utf8 = False
                line_reader = SerialLineReader()

                while True:
                    if time.monotonic() - last_time_sync >= args.time_sync_interval:
                        send_time_sync(connection)
                        last_time_sync = time.monotonic()
                    available = connection.in_waiting
                    raw_chunk = connection.read(min(available, 4096) if available else 1)
                    if not raw_chunk:
                        continue
                    for line in line_reader.feed(raw_chunk):
                        if line is None:
                            if in_bridge_frame:
                                frame_has_invalid_utf8 = True
                            continue
                        if line == BRIDGE_START:
                            buffer = []
                            in_bridge_frame = True
                            frame_has_invalid_utf8 = False
                            continue
                        if line == BRIDGE_END:
                            if not in_bridge_frame:
                                buffer = []
                                continue
                            in_bridge_frame = False
                            path = buffer[1].strip() if len(buffer) >= 2 else ""
                            if frame_has_invalid_utf8:
                                print(
                                    "[serial] Dropping serial frame before HTTP: invalid UTF-8 bytes.",
                                    flush=True,
                                )
                                status, response_body = 400, ""
                            else:
                                status, response_body = forward_frame(
                                    buffer,
                                    args.base_url,
                                    args.token,
                                    args.timeout,
                                    backend_ssl_context,
                                )
                            send_bridge_response(connection, status, response_body, path)
                            buffer = []
                            frame_has_invalid_utf8 = False
                            continue
                        if in_bridge_frame:
                            buffer.append(line)
                        elif args.show_device_logs:
                            print(f"[esp32] {line}", flush=True)
        except KeyboardInterrupt:
            print("[serial] Serial bridge stopped.", flush=True)
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"[serial] {exc}", flush=True)
            if not args.retry:
                return 1
            time.sleep(args.retry_interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Forward ESP32 USB serial bridge frames to the local backend.")
    parser.add_argument("--port", default=os.getenv("ESP32_SERIAL_PORT", "auto"))
    parser.add_argument("--baud", type=int, default=int(os.getenv("ESP32_SERIAL_BAUD", "115200")))
    parser.add_argument("--base-url", default=os.getenv("ESP32_BRIDGE_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--ca-file", default=os.getenv("ESP32_BRIDGE_CA_FILE"))
    parser.add_argument("--scanner-id", default=os.getenv("LOCAL_SCANNER_ID", "scn_dev_lab_001"))
    parser.add_argument(
        "--token",
        default=os.getenv("LOCAL_SCANNER_TOKEN") or os.getenv("SCANNER_TOKEN", ""),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("ESP32_BRIDGE_TIMEOUT", str(DEFAULT_BACKEND_TIMEOUT_SECONDS))),
    )
    parser.add_argument(
        "--time-sync-interval",
        type=float,
        default=float(os.getenv("ESP32_TIME_SYNC_INTERVAL_SECONDS", "60")),
    )
    parser.add_argument("--retry-interval", type=float, default=float(os.getenv("ESP32_SERIAL_RETRY_SECONDS", "2")))
    parser.add_argument("--no-retry", dest="retry", action="store_false")
    parser.add_argument("--show-device-logs", action="store_true", default=truthy(os.getenv("ESP32_SERIAL_SHOW_LOGS")))
    parser.set_defaults(retry=True)
    return parser


def main() -> int:
    load_env()
    parser = build_parser()
    args = parser.parse_args()
    if not args.token:
        print("Scanner token is empty. Set LOCAL_SCANNER_TOKEN in .env.", flush=True)
        return 2
    return run_bridge(args)


if __name__ == "__main__":
    raise SystemExit(main())
