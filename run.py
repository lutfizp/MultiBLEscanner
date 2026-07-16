from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
import fcntl
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUN_LOCK_PATH = ROOT / ".bluetooth_scanner.run.lock"


def project_venv_python() -> Path:
    executable = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return executable


def use_project_venv() -> None:
    executable = project_venv_python()
    if not executable.exists() or Path(sys.executable).resolve() == executable.resolve():
        return
    os.execv(str(executable), [str(executable), str(Path(__file__).resolve()), *sys.argv[1:]])


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


def bootstrap_database() -> dict[str, object]:
    from alembic import command
    from alembic.config import Config

    from backend.app import models  # noqa: F401
    from backend.app.database import Base, engine
    from backend.app.seed import ensure_local_scanner

    alembic_config = Config(str(ROOT / "backend" / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    command.upgrade(alembic_config, "head")
    Base.metadata.create_all(bind=engine)
    return ensure_local_scanner()


def acquire_runner_lock():
    handle = RUN_LOCK_PATH.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.seek(0)
        owner = handle.read().strip()
        handle.close()
        suffix = f" (PID {owner})" if owner.isdigit() else ""
        raise RuntimeError(f"Another Bluetooth Scanner runner is already active{suffix}.") from exc
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def choose_port(host: str, preferred_port: int) -> int:
    for port in range(preferred_port, preferred_port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"No free TCP port found from {preferred_port} to {preferred_port + 19}.")


def main() -> None:
    use_project_venv()
    load_env()
    runner_lock = acquire_runner_lock()
    try:
        seed_result = bootstrap_database()

        host = os.getenv("RUN_HOST", "127.0.0.1")
        requested_port = int(os.getenv("RUN_PORT", "8000"))
        port = choose_port(host, requested_port)
        serial_process: subprocess.Popen[bytes] | None = None

        def local_api_host() -> str:
            return "127.0.0.1" if host in {"0.0.0.0", "::"} else host

        def serial_enabled() -> bool:
            return os.getenv("ESP32_SERIAL_ENABLED", "true").lower() in {"1", "true", "yes", "on"}

        def start_serial_bridge() -> None:
            nonlocal serial_process
            time.sleep(float(os.getenv("ESP32_SERIAL_START_DELAY", "2.5")))
            if not serial_enabled():
                return
            serial_process = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "serial_bridge.py"),
                    "--base-url",
                    f"http://{local_api_host()}:{port}",
                    "--scanner-id",
                    str(seed_result["scanner_id"]),
                    "--token",
                    str(seed_result["scanner_token"]),
                    "--port",
                    os.getenv("ESP32_SERIAL_PORT", "auto"),
                    "--baud",
                    os.getenv("ESP32_SERIAL_BAUD", "115200"),
                ],
                cwd=str(ROOT),
            )

        print("Bluetooth Scanner ready")
        if port != requested_port:
            print(f"Requested port {requested_port} is busy. Using {port} instead.")
        print(f"Dashboard: http://{local_api_host()}:{port}/dashboard/")
        print("Mode: real ESP32 over USB serial.")
        print(f"Scanner ID: {seed_result['scanner_id']}")
        print(f"Serial bridge: {'enabled' if serial_enabled() else 'disabled'}")
        if serial_enabled():
            print(f"Serial port: {os.getenv('ESP32_SERIAL_PORT', 'auto')}")

        import uvicorn

        from backend.app.realtime import broker

        class ScannerServer(uvicorn.Server):
            def handle_exit(self, sig: int, frame: object | None) -> None:
                # Close long-lived SSE responses before Uvicorn waits for open
                # HTTP connections, otherwise Ctrl+C can leave the runner lock.
                broker.request_shutdown()
                super().handle_exit(sig, frame)

        threading.Thread(target=start_serial_bridge, daemon=True).start()
        config = uvicorn.Config("backend.app.main:app", host=host, port=port, reload=False)
        try:
            ScannerServer(config).run()
        except KeyboardInterrupt:
            pass
    finally:
        if "serial_process" in locals() and serial_process and serial_process.poll() is None:
            serial_process.terminate()
            try:
                serial_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                serial_process.kill()
        fcntl.flock(runner_lock.fileno(), fcntl.LOCK_UN)
        runner_lock.close()


if __name__ == "__main__":
    try:
        main()
    except ModuleNotFoundError as exc:
        print(
            f"Bluetooth Scanner dependency missing: {exc.name}. Run 'python3 setup_project.py backend'.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
    except RuntimeError as exc:
        print(f"Bluetooth Scanner did not start: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
