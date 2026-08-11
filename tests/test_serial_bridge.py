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


def test_firmware_keeps_radio_and_gatt_work_bounded_and_independent():
    firmware = (ROOT / "firmware" / "src" / "main.cpp").read_text(encoding="utf-8")
    setup_start = firmware.index("void setup()")
    loop_start = firmware.index("void loop()")
    setup_body = firmware[setup_start:loop_start]
    loop_body = firmware[loop_start:]
    enrichment_start = firmware.index("void enrichNextTarget()")
    enrichment_body = firmware[enrichment_start:firmware.index("bool waitForBridgeResponse", enrichment_start)]
    transport_start = firmware.index("void transportTask(void *)")
    transport_body = firmware[transport_start:setup_start]

    assert "startContinuousScan(false)" in setup_body
    assert "superviseContinuousScan(now);" in loop_body
    assert "scan->getResults(" not in firmware
    assert "GATT_ENRICHMENT_MIN_RSSI_DBM" in firmware
    assert "xTaskNotifyGive(gattWorkerTaskHandle);" in enrichment_body
    assert "xTaskCreate(" not in enrichment_body
    assert "gattWorkerBlocksUpload" not in firmware
    assert "size_t observationBacklog = observationBacklogCount();" in transport_body
    assert "uploadGattEnrichment();" in transport_body
    assert "TrackingSchedule tracking = snapshotTrackingSchedule();" in transport_body
    assert "TrackingConfig tracking = snapshotTrackingConfig();" not in transport_body


def test_firmware_drains_new_backlog_without_tight_retry_loops():
    firmware = (ROOT / "firmware" / "src" / "main.cpp").read_text(encoding="utf-8")
    transport_start = firmware.index("void transportTask(void *)")
    setup_start = firmware.index("void setup()")
    transport_body = firmware[transport_start:setup_start]

    assert "size_t observationBacklogCount()" in firmware
    assert "size_t observationFrameCapacity()" in firmware
    assert "bool observationRetryPending = pendingBatchId.length() > 0;" in transport_body
    assert "!observationRetryPending" in transport_body
    assert "observationBacklog > observationFrameCapacity()" in transport_body
    assert "transportBacklogDrainCount++;" in transport_body
    assert "StaticJsonDocument<OBSERVATION_UPLOAD_JSON_CAPACITY> doc;" in firmware
    assert "DynamicJsonDocument doc(documentCapacity);" not in firmware
    config = (ROOT / "firmware" / "include" / "config.example.h").read_text(encoding="utf-8")
    assert "#define OBSERVATION_BUFFER_SIZE 96" in config
    assert "#define MAX_SERIAL_FRAME_OBSERVATIONS 12" in config
    assert "#define TRANSPORT_TASK_STACK_SIZE 32768" in config
    assert "#define SERIAL_BAUD_RATE 230400" in config
    assert "#define SERIAL_RX_BUFFER_SIZE 4096" in config
    assert "#define SERIAL_CONTROL_MAX_BYTES 4096" in config
    assert "StaticJsonDocument<12288> doc;" in firmware
    assert "Serial.setRxBufferSize(SERIAL_RX_BUFFER_SIZE);" in firmware
    assert 'health["serial_control_overflow_count"] = serialControlOverflowCount;' in firmware
    assert "Serial.begin(SERIAL_BAUD_RATE);" in firmware
    assert 'pendingBatchId = "";' in firmware
    assert "uploadOversizedObservationDropCount++;" in firmware


def test_firmware_transport_ids_are_unique_across_reboots():
    firmware = (ROOT / "firmware" / "src" / "main.cpp").read_text(encoding="utf-8")
    make_id_start = firmware.index("String makeId(const char *prefix)")
    make_id_end = firmware.index("bool pushObservation", make_id_start)
    make_id_body = firmware[make_id_start:make_id_end]

    assert 'String(prefix) + "-" + SCANNER_ID + "-" + bootId' in make_id_body
    assert firmware.index("bootId =", firmware.index("void setup()")) < firmware.index(
        "xTaskCreate(\n    transportTask,",
        firmware.index("void setup()"),
    )


def test_runner_and_firmware_share_the_safe_serial_baud():
    runner = (ROOT / "run.py").read_text(encoding="utf-8")
    platformio = (ROOT / "firmware" / "platformio.ini").read_text(encoding="utf-8")

    assert 'os.getenv("ESP32_SERIAL_BAUD", "230400")' in runner
    assert "monitor_speed = 230400" in platformio
    assert "upload_speed = 115200" in platformio


def test_firmware_heartbeat_does_not_require_a_second_heap_copy():
    firmware = (ROOT / "firmware" / "src" / "main.cpp").read_text(encoding="utf-8")
    heartbeat_start = firmware.index("bool sendHeartbeat()")
    heartbeat_end = firmware.index("void writeGattEnrichmentPayload", heartbeat_start)
    heartbeat_body = firmware[heartbeat_start:heartbeat_end]

    assert "StaticJsonDocument<6144> doc;" in heartbeat_body
    assert "doc.overflowed()" in heartbeat_body
    assert "httpRequestJson(" in heartbeat_body
    assert "serializeJson(doc, body);" not in heartbeat_body


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


def test_forward_frame_rejects_incomplete_gatt_enrichment_before_backend_request(capsys):
    status, response = forward_frame(
        [
            "POST",
            "/api/scanners/scn/enrichments",
            '{"report_id":"gatt-1","source_observation_id":"obs-1","gatt_enrichment":{}}',
        ],
        "http://127.0.0.1:1",
        "token",
        1,
    )

    assert (status, response) == (400, "")
    assert "Dropping invalid GATT enrichment frame before HTTP" in capsys.readouterr().out


def test_forward_frame_does_not_log_every_success(monkeypatch, capsys):
    monkeypatch.setattr(
        "serial_bridge.send_to_backend",
        lambda *_args, **_kwargs: (200, '{"accepted":true}'),
    )

    status, response = forward_frame(
        [
            "POST",
            "/api/scanners/scn/observations/batch",
            '{"batch_id":"batch-1","observations":[{"observation_id":"obs-1"}]}',
        ],
        "http://127.0.0.1:8000",
        "token",
        1,
    )

    assert (status, response) == (200, '{"accepted":true}')
    assert capsys.readouterr().out == ""
