from pathlib import Path


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


def test_alembic_script_location_is_independent_from_working_directory():
    from alembic.config import Config

    project_root = Path(__file__).resolve().parents[1]
    config = Config(str(project_root / "backend" / "alembic.ini"))

    assert Path(config.get_main_option("script_location")) == (
        project_root / "backend" / "migrations"
    )
