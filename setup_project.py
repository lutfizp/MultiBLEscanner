from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import subprocess
import sys
import venv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
VENV_DIR = PROJECT_ROOT / ".venv"
LOCAL_TLS_DIR = PROJECT_ROOT / ".local" / "tls"
SCANNER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{3,64}$")


def venv_python() -> Path:
    return VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def project_path(value: str, project_root: Path = PROJECT_ROOT) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else project_root / path


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

    if values.get("ESP32_BRIDGE_TIMEOUT") == "60":
        existing_lines = [
            "ESP32_BRIDGE_TIMEOUT=8"
            if line.startswith("ESP32_BRIDGE_TIMEOUT=")
            else line
            for line in existing_lines
        ]
        values["ESP32_BRIDGE_TIMEOUT"] = "8"

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
        "RUN_HTTPS": "false",
        "RUN_TLS_CERTFILE": ".local/tls/localhost.pem",
        "RUN_TLS_KEYFILE": ".local/tls/localhost-key.pem",
        "RUN_TLS_CA_FILE": ".local/tls/local-ca.pem",
        "ESP32_SERIAL_ENABLED": "true",
        "ESP32_SERIAL_PORT": "auto",
        "ESP32_SERIAL_BAUD": "115200",
        "ESP32_SERIAL_START_DELAY": "2.5",
        "ESP32_SERIAL_RETRY_SECONDS": "2",
        "ESP32_BRIDGE_TIMEOUT": "8",
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


def update_environment_values(
    updates: dict[str, str],
    project_root: Path = PROJECT_ROOT,
) -> dict[str, str]:
    env_path = project_root / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    remaining = dict(updates)
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    if output and remaining:
        output.append("")
    output.extend(f"{key}={value}" for key, value in remaining.items())
    env_path.write_text("\n".join(output).rstrip() + "\n", encoding="utf-8")
    return parse_env(env_path.read_text(encoding="utf-8").splitlines())


def certificate_is_valid(path: Path, minimum_seconds: int = 30 * 24 * 60 * 60) -> bool:
    if not path.exists():
        return False
    openssl = shutil.which("openssl")
    if openssl is None:
        return False
    result = subprocess.run(
        [openssl, "x509", "-checkend", str(minimum_seconds), "-noout", "-in", str(path)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def ensure_local_tls(
    project_root: Path = PROJECT_ROOT,
    tls_dir: Path | None = None,
) -> dict[str, Path]:
    openssl = shutil.which("openssl")
    if openssl is None:
        raise RuntimeError("OpenSSL is required to create the local HTTPS certificate")

    directory = tls_dir or project_root / ".local" / "tls"
    directory.mkdir(parents=True, exist_ok=True)
    ca_cert = directory / "local-ca.pem"
    ca_key = directory / "local-ca-key.pem"
    server_cert = directory / "localhost.pem"
    server_key = directory / "localhost-key.pem"
    server_csr = directory / "localhost.csr"
    extensions = directory / "localhost.ext"

    ca_ready = ca_key.exists() and certificate_is_valid(ca_cert, 90 * 24 * 60 * 60)
    if not ca_ready:
        for path in (ca_cert, ca_key, server_cert, server_key, server_csr):
            path.unlink(missing_ok=True)
        subprocess.run(
            [
                openssl,
                "req",
                "-x509",
                "-new",
                "-nodes",
                "-newkey",
                "rsa:3072",
                "-sha256",
                "-days",
                "3650",
                "-keyout",
                str(ca_key),
                "-out",
                str(ca_cert),
                "-subj",
                "/CN=Bluetooth Scanner Local CA",
                "-addext",
                "basicConstraints=critical,CA:TRUE,pathlen:0",
                "-addext",
                "keyUsage=critical,keyCertSign,cRLSign",
                "-addext",
                "subjectKeyIdentifier=hash",
            ],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )

    server_ready = server_key.exists() and certificate_is_valid(server_cert)
    if not server_ready:
        server_cert.unlink(missing_ok=True)
        server_key.unlink(missing_ok=True)
        server_csr.unlink(missing_ok=True)
        extensions.write_text(
            "\n".join(
                [
                    "authorityKeyIdentifier=keyid,issuer",
                    "basicConstraints=critical,CA:FALSE",
                    "keyUsage=critical,digitalSignature,keyEncipherment",
                    "extendedKeyUsage=serverAuth",
                    "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1",
                    "",
                ]
            ),
            encoding="ascii",
        )
        try:
            subprocess.run(
                [
                    openssl,
                    "req",
                    "-new",
                    "-nodes",
                    "-newkey",
                    "rsa:2048",
                    "-keyout",
                    str(server_key),
                    "-out",
                    str(server_csr),
                    "-subj",
                    "/CN=localhost",
                ],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    openssl,
                    "x509",
                    "-req",
                    "-sha256",
                    "-days",
                    "397",
                    "-in",
                    str(server_csr),
                    "-CA",
                    str(ca_cert),
                    "-CAkey",
                    str(ca_key),
                    "-set_serial",
                    f"0x{secrets.token_hex(16)}",
                    "-extfile",
                    str(extensions),
                    "-out",
                    str(server_cert),
                ],
                cwd=project_root,
                check=True,
                capture_output=True,
                text=True,
            )
        finally:
            server_csr.unlink(missing_ok=True)
            extensions.unlink(missing_ok=True)

    subprocess.run(
        [openssl, "verify", "-CAfile", str(ca_cert), str(server_cert)],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    ca_key.chmod(0o600)
    server_key.chmod(0o600)
    return {
        "ca_cert": ca_cert,
        "ca_key": ca_key,
        "server_cert": server_cert,
        "server_key": server_key,
    }


def trust_local_ca(ca_cert: Path) -> None:
    if sys.platform != "darwin":
        print(f"Setup: trust {ca_cert} in the browser or operating-system trust store")
        return
    security = shutil.which("security")
    if security is None:
        raise RuntimeError("macOS security tool is required to trust the local HTTPS CA")
    keychain = Path.home() / "Library" / "Keychains" / "login.keychain-db"
    subprocess.run(
        [
            security,
            "add-trusted-cert",
            "-r",
            "trustRoot",
            "-p",
            "ssl",
            "-k",
            str(keychain),
            str(ca_cert),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )


def ensure_firmware_config(scanner_id: str, project_root: Path = PROJECT_ROOT) -> Path:
    if not SCANNER_ID_PATTERN.fullmatch(scanner_id):
        raise RuntimeError(f"Invalid LOCAL_SCANNER_ID for firmware: {scanner_id!r}")
    example_path = project_root / "firmware" / "include" / "config.example.h"
    config_path = project_root / "firmware" / "include" / "config.h"
    source = example_path.read_text(encoding="utf-8")
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
        choices=("backend", "firmware", "flash", "https", "all"),
        default="backend",
        help="backend initializes SQLite; firmware builds; flash uploads; https provisions local TLS; all runs every setup",
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
    include_https = args.target in {"https", "all"}
    upload_firmware = args.target in {"flash", "all"}

    executable = ensure_venv()
    values = ensure_environment_file()
    if not args.skip_dependencies and (include_backend or include_firmware):
        install_requirements(executable, include_firmware)
    elif include_firmware:
        run([str(executable), "-m", "platformio", "--version"])

    if include_backend:
        bootstrap_backend(executable)
    if include_https:
        tls = ensure_local_tls()
        trust_local_ca(tls["ca_cert"])
        values = update_environment_values(
            {
                "RUN_HTTPS": "true",
                "RUN_TLS_CERTFILE": ".local/tls/localhost.pem",
                "RUN_TLS_KEYFILE": ".local/tls/localhost-key.pem",
                "RUN_TLS_CA_FILE": ".local/tls/local-ca.pem",
            }
        )
        print("Setup: trusted local HTTPS for localhost and 127.0.0.1")
    if include_firmware:
        ensure_firmware_config(values["LOCAL_SCANNER_ID"])
        build_firmware(executable, upload_firmware, args.port)

    print(f"Setup: {args.target} complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
