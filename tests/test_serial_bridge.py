import json
import re
from pathlib import Path

from serial_bridge import (
    BRIDGE_END,
    BRIDGE_START,
    DEFAULT_BACKEND_TIMEOUT_SECONDS,
    SerialLineReader,
    build_parser,
    build_backend_ssl_context,
    forward_frame,
)

ROOT = Path(__file__).resolve().parents[1]


def test_http_backend_does_not_create_an_ssl_context():
    assert build_backend_ssl_context("http://127.0.0.1:8000", None) is None


def test_https_backend_rejects_a_missing_ca_file(tmp_path):
    missing = tmp_path / "missing-ca.pem"

    try:
        build_backend_ssl_context("https://127.0.0.1:8000", str(missing))
    except RuntimeError as exc:
        assert str(missing) in str(exc)
    else:
        raise AssertionError("missing HTTPS CA was accepted")


def test_bridge_timeouts_cannot_cross_the_presence_missing_threshold(monkeypatch):
    monkeypatch.delenv("ESP32_BRIDGE_TIMEOUT", raising=False)
    bridge_timeout = build_parser().parse_args([]).timeout
    firmware_config = (ROOT / "firmware" / "include" / "config.example.h").read_text(
        encoding="utf-8",
    )
    firmware_timeout_ms = int(
        re.search(
            r"#define SERIAL_BRIDGE_RESPONSE_TIMEOUT_MS (\d+)",
            firmware_config,
        ).group(1),
    )
    presence_missing_seconds = int(
        re.search(
            r"^PRESENCE_MISSING_SECONDS=(\d+)$",
            (ROOT / ".env.example").read_text(encoding="utf-8"),
            re.MULTILINE,
        ).group(1),
    )

    assert bridge_timeout == DEFAULT_BACKEND_TIMEOUT_SECONDS
    assert bridge_timeout * 1000 < firmware_timeout_ms
    assert firmware_timeout_ms < presence_missing_seconds * 1000


def test_firmware_transport_waits_only_in_the_dedicated_task():
    firmware = (ROOT / "firmware" / "src" / "main.cpp").read_text(encoding="utf-8")
    setup_start = firmware.index("void setup()")
    loop_start = firmware.index("void loop()")
    setup_body = firmware[setup_start:loop_start]
    loop_body = firmware[loop_start:]

    assert "void transportTask(void *)" in firmware
    assert "xTaskCreate(\n    transportTask," in setup_body
    assert "uploadBatch();" not in loop_body
    assert "uploadTrackingBatch();" not in loop_body
    assert "fetchConfig();" not in loop_body
    assert "sendHeartbeat();" not in loop_body


def test_serial_line_reader_does_not_insert_newlines_into_split_json_body():
    body = json.dumps({"batch_id": "batch-1", "observations": [{"raw": "x" * 28_000}]})
    wire = f"{BRIDGE_START}\nPOST\n/api/scanners/scn/observations/batch\n{body}\n{BRIDGE_END}\n".encode()
    reader = SerialLineReader()
    lines: list[str | None] = []

    for start in range(0, len(wire), 137):
        lines.extend(reader.feed(wire[start : start + 137]))

    assert lines == [BRIDGE_START, "POST", "/api/scanners/scn/observations/batch", body, BRIDGE_END]
    assert json.loads(lines[3]) == {"batch_id": "batch-1", "observations": [{"raw": "x" * 28_000}]}


def test_serial_line_reader_marks_invalid_utf8_instead_of_replacing_it():
    reader = SerialLineReader()

    lines = reader.feed(b"valid\ninvalid-\xff-name\n")

    assert lines == ["valid", None]


def test_forward_frame_rejects_invalid_json_before_backend_request(capsys):
    status, response = forward_frame(
        ["POST", "/api/scanners/scn/observations/batch", '{"observations":[{"raw":"cut'],
        "http://127.0.0.1:1",
        "token",
        1,
    )

    assert (status, response) == (400, "")
    assert "Dropping malformed JSON frame before HTTP" in capsys.readouterr().out


def test_forward_frame_rejects_empty_observation_batch_before_backend_request(capsys):
    status, response = forward_frame(
        ["POST", "/api/scanners/scn/observations/batch", "{}"],
        "http://127.0.0.1:1",
        "token",
        1,
    )

    assert (status, response) == (400, "")
    assert "Dropping invalid observation batch frame before HTTP" in capsys.readouterr().out


def test_forward_frame_rejects_empty_tracking_batch_before_backend_request(capsys):
    status, response = forward_frame(
        [
            "POST",
            "/api/scanners/scn/tracking-samples/batch",
            '{"batch_id":"focus-1","session_id":"session-1","samples":[]}',
        ],
        "http://127.0.0.1:1",
        "token",
        1,
    )

    assert (status, response) == (400, "")
    assert "Dropping invalid tracking batch frame before HTTP" in capsys.readouterr().out
