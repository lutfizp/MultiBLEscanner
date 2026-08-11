import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def run_alembic(database_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
    environment["PYTHONPYCACHEPREFIX"] = "/tmp/bluetooth-scanner-pycache"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ROOT / "backend" / "alembic.ini"),
            *arguments,
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def run_migrations(database_path: Path, revision: str) -> None:
    run_alembic(database_path, "upgrade", revision)


def test_default_sqlite_path_is_resolved_from_project_data_directory():
    from backend.app.config import Settings

    settings = Settings(_env_file=None, database_url=None, bluetooth_scanner_data_dir="data")

    assert settings.data_path == settings.project_root / "data"
    database_path = settings.project_root / "data" / "bluetooth_scanner.sqlite3"
    assert settings.resolved_database_url == f"sqlite:///{database_path.as_posix()}"


def test_relative_sqlite_override_is_resolved_from_project_root():
    from backend.app.config import Settings

    settings = Settings(_env_file=None, database_url="sqlite:///state/custom.sqlite3")

    assert settings.resolved_database_url == f"sqlite:///{(settings.project_root / 'state' / 'custom.sqlite3').as_posix()}"


def test_environment_setup_normalizes_legacy_root_sqlite_without_changing_database(
    tmp_path: Path,
):
    from setup_project import ensure_environment_file

    (tmp_path / "bluetooth_scanner.sqlite3").touch()
    (tmp_path / ".env").write_text(
        "DATABASE_URL=sqlite:///bluetooth_scanner.sqlite3\nLOCAL_SCANNER_TOKEN=retained-token\n",
        encoding="utf-8",
    )

    values = ensure_environment_file(tmp_path)
    content = (tmp_path / ".env").read_text(encoding="utf-8")

    assert "DATABASE_URL=" not in content
    assert values["BLUETOOTH_SCANNER_DATA_DIR"] == "."
    assert values["LOCAL_SCANNER_TOKEN"] == "retained-token"


def test_environment_update_enables_https_without_replacing_existing_secrets(tmp_path: Path):
    from setup_project import update_environment_values

    (tmp_path / ".env").write_text(
        "LOCAL_SCANNER_TOKEN=retained-token\nRUN_HTTPS=false\n",
        encoding="utf-8",
    )

    values = update_environment_values(
        {
            "RUN_HTTPS": "true",
            "RUN_TLS_CERTFILE": ".local/tls/localhost.pem",
        },
        tmp_path,
    )

    assert values["LOCAL_SCANNER_TOKEN"] == "retained-token"
    assert values["RUN_HTTPS"] == "true"
    assert values["RUN_TLS_CERTFILE"] == ".local/tls/localhost.pem"


def test_environment_setup_replaces_only_the_legacy_bridge_timeout(tmp_path: Path):
    from setup_project import ensure_environment_file

    (tmp_path / ".env").write_text(
        "LOCAL_SCANNER_TOKEN=retained-token\nESP32_BRIDGE_TIMEOUT=60\n",
        encoding="utf-8",
    )

    values = ensure_environment_file(tmp_path)

    assert values["LOCAL_SCANNER_TOKEN"] == "retained-token"
    assert values["ESP32_BRIDGE_TIMEOUT"] == "8"
    assert "ESP32_BRIDGE_TIMEOUT=8" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_environment_setup_replaces_previous_managed_serial_baud(tmp_path: Path):
    from setup_project import ensure_environment_file

    (tmp_path / ".env").write_text(
        "LOCAL_SCANNER_TOKEN=retained-token\nESP32_SERIAL_BAUD=460800\n",
        encoding="utf-8",
    )

    values = ensure_environment_file(tmp_path)

    assert values["LOCAL_SCANNER_TOKEN"] == "retained-token"
    assert values["ESP32_SERIAL_BAUD"] == "230400"
    assert "ESP32_SERIAL_BAUD=230400" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_local_tls_certificate_covers_loopback_hosts(tmp_path: Path):
    from setup_project import ensure_local_tls

    try:
        paths = ensure_local_tls(tmp_path, tmp_path / ".local" / "tls")
    except RuntimeError as exc:
        if "OpenSSL is required" in str(exc):
            pytest.skip(str(exc))
        raise

    assert paths["ca_cert"].is_file()
    assert paths["server_cert"].is_file()
    assert paths["server_key"].is_file()
    assert paths["server_key"].stat().st_mode & 0o077 == 0


def test_firmware_config_uses_local_scanner_identity(tmp_path: Path):
    from setup_project import ensure_firmware_config

    include_dir = tmp_path / "firmware" / "include"
    include_dir.mkdir(parents=True)
    (include_dir / "config.example.h").write_text(
        '#pragma once\n#define SCANNER_ID "scn_replace_me"\n',
        encoding="utf-8",
    )

    config_path = ensure_firmware_config("scn_engineering_001", tmp_path)

    assert config_path.read_text(encoding="utf-8") == (
        '#pragma once\n#define SCANNER_ID "scn_engineering_001"\n'
    )


def test_firmware_config_refreshes_managed_release_constants(tmp_path: Path):
    from setup_project import ensure_firmware_config

    include_dir = tmp_path / "firmware" / "include"
    include_dir.mkdir(parents=True)
    (include_dir / "config.example.h").write_text(
        "\n".join(
            [
                '#define SCANNER_ID "scn_replace_me"',
                '#define FIRMWARE_VERSION "esp32-ble-scanner-1.7.0"',
                "#define SERIAL_BRIDGE_RESPONSE_TIMEOUT_MS 12000",
                "",
            ],
        ),
        encoding="utf-8",
    )
    (include_dir / "config.h").write_text(
        "\n".join(
            [
                '#define SCANNER_ID "old-scanner"',
                '#define FIRMWARE_VERSION "esp32-ble-scanner-1.4.1"',
                "#define SERIAL_BRIDGE_RESPONSE_TIMEOUT_MS 60000",
                "",
            ],
        ),
        encoding="utf-8",
    )

    config_path = ensure_firmware_config("scn_engineering_001", tmp_path)
    content = config_path.read_text(encoding="utf-8")

    assert '#define SCANNER_ID "scn_engineering_001"' in content
    assert '#define FIRMWARE_VERSION "esp32-ble-scanner-1.7.0"' in content
    assert "#define SERIAL_BRIDGE_RESPONSE_TIMEOUT_MS 12000" in content
    assert "60000" not in content


def test_alembic_script_location_is_independent_from_working_directory():
    from alembic.config import Config

    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "backend" / "alembic.ini"))

    assert Path(config.get_main_option("script_location")) == (
        project_root / "backend" / "migrations"
    )


def test_fresh_alembic_upgrade_creates_only_the_current_schema(tmp_path: Path):
    database_path = tmp_path / "fresh.sqlite3"

    run_migrations(database_path, "head")
    schema_check = run_alembic(database_path, "check")

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        scanner_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(scanners)")
        }
        config_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(scanner_configurations)")
        }
        identity_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(observed_identities)")
        }
        logical_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(logical_devices)")
        }
        decision_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(manual_device_correlation_decisions)"
            )
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    assert revision == "0011"
    assert "No new upgrade operations detected" in (
        schema_check.stdout + schema_check.stderr
    )
    assert "monitored_locations" not in tables
    assert "location_id" not in scanner_columns
    assert {"presence_missing_seconds", "presence_offline_seconds", "extra"}.isdisjoint(
        config_columns
    )
    assert "fingerprint" not in identity_columns
    assert "identity_signature" not in logical_columns
    assert "correlation_id" in decision_columns


def test_schema_cleanup_preserves_legacy_scanner_location_and_identity_rows(
    tmp_path: Path,
):
    database_path = tmp_path / "upgrade.sqlite3"
    run_migrations(database_path, "0010")

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO monitored_locations "
            "(id, name, building, floor, room, zone, latitude, longitude, indoor_x, indoor_y) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "location-1",
                "Legacy location",
                "Building A",
                "2",
                "Room 201",
                "North",
                -6.2,
                106.8,
                12.5,
                8.5,
            ),
        )
        connection.execute(
            "INSERT INTO scanners "
            "(id, display_name, hardware_id, token_hash, location_id, network_info, status, config_version, enabled) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "scanner-1",
                "Legacy scanner",
                "legacy-hardware",
                "token-hash",
                "location-1",
                "{}",
                "registered",
                1,
                1,
            ),
        )
        connection.execute(
            "INSERT INTO scanner_configurations "
            "(scanner_id, version, scan_interval_ms, upload_interval_seconds, batch_size, rssi_min, "
            "presence_missing_seconds, presence_offline_seconds, extra) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("scanner-1", 1, 5000, 5, 40, -110, 45, 180, "{}"),
        )
        connection.execute(
            "INSERT INTO observed_identities "
            "(id, address, address_type, service_uuids, service_data, advertising_flags, "
            "randomized_address, fingerprint, first_seen_at, last_seen_at, observation_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "identity-1",
                "80:e1:26:9e:3e:e3",
                "public",
                "[]",
                "{}",
                "{}",
                0,
                "legacy-fingerprint",
                "2026-01-01 00:00:00",
                "2026-01-01 00:00:00",
                1,
            ),
        )
        connection.execute(
            "INSERT INTO logical_devices "
            "(id, primary_address, primary_address_type, display_name, status, movement_status, "
            "known, ignored, identity_confidence, location_confidence, movement_confidence, "
            "proximity_band, first_seen_at, last_seen_at, observation_count, identity_signature, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "device-1",
                "80:e1:26:9e:3e:e3",
                "public",
                "Legacy device",
                "active",
                "stationary",
                0,
                0,
                0.9,
                0.0,
                0.0,
                "signal_moderate",
                "2026-01-01 00:00:00",
                "2026-01-01 00:00:00",
                1,
                "{}",
                "[]",
            ),
        )
        connection.commit()

    run_migrations(database_path, "head")

    with sqlite3.connect(database_path) as connection:
        scanner = connection.execute(
            "SELECT building, floor, room, zone, latitude, longitude, indoor_x, indoor_y, location_source "
            "FROM scanners WHERE id = 'scanner-1'"
        ).fetchone()
        identity_count = connection.execute(
            "SELECT count(*) FROM observed_identities WHERE id = 'identity-1'"
        ).fetchone()[0]
        device_count = connection.execute(
            "SELECT count(*) FROM logical_devices WHERE id = 'device-1'"
        ).fetchone()[0]

    assert scanner == (
        "Building A",
        "2",
        "Room 201",
        "North",
        -6.2,
        106.8,
        12.5,
        8.5,
        "configured",
    )
    assert identity_count == 1
    assert device_count == 1


def test_runner_bootstrap_uses_migrations_without_create_all_fallback():
    source = (ROOT / "run.py").read_text(encoding="utf-8")
    bootstrap = source[source.index("def bootstrap_database"):source.index("def acquire_runner_lock")]

    assert 'command.upgrade(alembic_config, "head")' in bootstrap
    assert "create_all" not in bootstrap
