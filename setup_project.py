from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import venv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
VENV_DIR = PROJECT_ROOT / ".venv"
SCANNER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{3,64}$")


def venv_python() -> Path:
    return VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def ensure_venv() -> Path:
    executable = venv_python()
    if not executable.exists():
        print(f"Setup: creating virtual environment at {VENV_DIR}")
        venv.EnvBuilder(with_pip=True).create(VENV_DIR)
    return executable


def install_requirements(executable: Path, include_firmware: bool) -> None:
    run([str(executable), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(executable), "-m", "pip", "install", "-r", "requirements.txt"])
    if include_firmware:
        run([str(executable), "-m", "pip", "install", "-r", "requirements-firmware.txt"])


def parse_env(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def ensure_environment_file(project_root: Path = PROJECT_ROOT) -> dict[str, str]:
    env_path = project_root / ".env"
    existing_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    values = parse_env(existing_lines)

    legacy_database_url = values.get("DATABASE_URL")
    if legacy_database_url == "sqlite:///bluetooth_scanner.sqlite3":
        existing_lines = [line for line in existing_lines if not line.startswith("DATABASE_URL=")]
        values.pop("DATABASE_URL", None)
        values.setdefault("BLUETOOTH_SCANNER_DATA_DIR", ".")

    default_data_dir = "." if (project_root / "bluetooth_scanner.sqlite3").exists() else "data"
    defaults = {
        "APP_NAME": "Bluetooth Scanner",
        "BLUETOOTH_SCANNER_DATA_DIR": default_data_dir,
        "SCANNER_REGISTRATION_SECRET": secrets.token_urlsafe(32),
        "SCANNER_TOKEN_SALT": secrets.token_urlsafe(32),
        "LOCAL_SCANNER_ID": "scn_dev_lab_001",
        "LOCAL_SCANNER_NAME": "USB ESP32 Scanner",
        "LOCAL_SCANNER_HARDWARE_ID": "usb-esp32-001",
        "LOCAL_SCANNER_INSTALLATION_NAME": "local-usb",
        "LOCAL_SCANNER_BUILDING": "Local",
        "LOCAL_SCANNER_FLOOR": "1",
        "LOCAL_SCANNER_ROOM": "ESP32",
        "LOCAL_SCANNER_ZONE": "USB Scanner",
        "LOCAL_SCANNER_TOKEN": secrets.token_urlsafe(32),
        "DASHBOARD_DIR": "dashboard",
        "APP_TIMEZONE": "Asia/Jakarta",
        "HEARTBEAT_TIMEOUT_SECONDS": "90",
        "PRESENCE_MISSING_SECONDS": "45",
        "PRESENCE_OFFLINE_SECONDS": "180",
        "RAW_OBSERVATION_RETENTION_DAYS": "30",
        "SUMMARY_RETENTION_DAYS": "365",
        "RUN_HOST": "127.0.0.1",
        "RUN_PORT": "8000",
        "ESP32_SERIAL_ENABLED": "true",
        "ESP32_SERIAL_PORT": "auto",
        "ESP32_SERIAL_BAUD": "115200",
        "ESP32_SERIAL_START_DELAY": "2.5",
        "ESP32_SERIAL_RETRY_SECONDS": "2",
        "ESP32_BRIDGE_TIMEOUT": "60",
    }

    appended: list[str] = []
    for key, value in defaults.items():
        if key not in values:
            values[key] = value
            appended.append(f"{key}={value}")

    output_lines = [*existing_lines]
    if output_lines and appended:
        output_lines.append("")
    output_lines.extend(appended)
    env_path.write_text("\n".join(output_lines).rstrip() + "\n", encoding="utf-8")
    return values


def ensure_firmware_config(scanner_id: str, project_root: Path = PROJECT_ROOT) -> Path:
    if not SCANNER_ID_PATTERN.fullmatch(scanner_id):
        raise RuntimeError(f"Invalid LOCAL_SCANNER_ID for firmware: {scanner_id!r}")
    example_path = project_root / "firmware" / "include" / "config.example.h"
    config_path = project_root / "firmware" / "include" / "config.h"
    source = (
        config_path.read_text(encoding="utf-8")
        if config_path.exists()
        else example_path.read_text(encoding="utf-8")
    )
    updated, count = re.subn(
        r'^#define SCANNER_ID "[^"]+"$',
        f'#define SCANNER_ID "{scanner_id}"',
        source,
        count=1,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise RuntimeError("Firmware config does not contain exactly one SCANNER_ID definition")
    config_path.write_text(updated, encoding="utf-8")
    return config_path


def bootstrap_backend(executable: Path) -> None:
    code = (
        "from run import load_env, bootstrap_database; "
        "load_env(); result = bootstrap_database(); "
        "print(f\"Setup: scanner {result['scanner_id']} is registered\")"
    )
    run([str(executable), "-c", code])


def resolve_serial_port(executable: Path, preferred: str) -> str:
    code = f"from serial_bridge import resolve_serial_port; print(resolve_serial_port({preferred!r}))"
    result = subprocess.run(
        [str(executable), "-c", code],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def build_firmware(executable: Path, upload: bool, preferred_port: str) -> None:
    command = [str(executable), "-m", "platformio", "run", "--project-dir", "firmware"]
    if upload:
        port = resolve_serial_port(executable, preferred_port)
        print(f"Setup: flashing firmware through {port}")
        command.extend(["-t", "upload", "--upload-port", port])
    else:
        print("Setup: building firmware")
    run(command)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare the Bluetooth Scanner backend and ESP32 firmware."
    )
    parser.add_argument(
        "target",
        nargs="?",
        choices=("backend", "firmware", "flash", "all"),
        default="backend",
        help="backend initializes SQLite; firmware builds; flash uploads; all initializes and uploads",
    )
    parser.add_argument("--port", default="auto", help="ESP32 serial port for flash, or auto")
    parser.add_argument(
        "--skip-dependencies",
        action="store_true",
        help="Use an existing .venv without running pip",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    include_backend = args.target in {"backend", "all"}
    include_firmware = args.target in {"firmware", "flash", "all"}
    upload_firmware = args.target in {"flash", "all"}

    executable = ensure_venv()
    values = ensure_environment_file()
    if not args.skip_dependencies:
        install_requirements(executable, include_firmware)
    elif include_firmware:
        run([str(executable), "-m", "platformio", "--version"])

    if include_backend:
        bootstrap_backend(executable)
    if include_firmware:
        ensure_firmware_config(values["LOCAL_SCANNER_ID"])
        build_firmware(executable, upload_firmware, args.port)

    print(f"Setup: {args.target} complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
