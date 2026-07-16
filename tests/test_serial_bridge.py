import json

from serial_bridge import BRIDGE_END, BRIDGE_START, SerialLineReader, forward_frame


def test_serial_line_reader_does_not_insert_newlines_into_split_json_body():
    body = json.dumps({"batch_id": "batch-1", "observations": [{"raw": "x" * 28_000}]})
    wire = f"{BRIDGE_START}\nPOST\n/api/scanners/scn/observations/batch\n{body}\n{BRIDGE_END}\n".encode()
    reader = SerialLineReader()
    lines: list[str] = []

    for start in range(0, len(wire), 137):
        lines.extend(reader.feed(wire[start : start + 137]))

    assert lines == [BRIDGE_START, "POST", "/api/scanners/scn/observations/batch", body, BRIDGE_END]
    assert json.loads(lines[3]) == {"batch_id": "batch-1", "observations": [{"raw": "x" * 28_000}]}


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
