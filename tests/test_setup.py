from pathlib import Path

import pytest


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
                '#define FIRMWARE_VERSION "esp32-ble-scanner-1.6.9"',
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
    assert '#define FIRMWARE_VERSION "esp32-ble-scanner-1.6.9"' in content
    assert "#define SERIAL_BRIDGE_RESPONSE_TIMEOUT_MS 12000" in content
    assert "60000" not in content


def test_alembic_script_location_is_independent_from_working_directory():
    from alembic.config import Config

    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "backend" / "alembic.ini"))

    assert Path(config.get_main_option("script_location")) == (
        project_root / "backend" / "migrations"
    )
