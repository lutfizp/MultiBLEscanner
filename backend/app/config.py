from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Bluetooth Scanner"
    database_url: Optional[str] = None
    bluetooth_scanner_data_dir: str = "data"
    scanner_registration_secret: Optional[str] = None
    scanner_token_salt: str = "development-token-salt"
    dashboard_dir: str = "dashboard"
    app_timezone: str = "Asia/Jakarta"
    local_scanner_id: str = "scn_dev_lab_001"
    heartbeat_timeout_seconds: int = 90
    presence_missing_seconds: int = 45
    presence_offline_seconds: int = 180
    raw_observation_retention_days: int = 30
    summary_retention_days: int = 365

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[2]

    @property
    def dashboard_path(self) -> Path:
        return self.project_root / self.dashboard_dir

    @property
    def data_path(self) -> Path:
        configured = Path(self.bluetooth_scanner_data_dir).expanduser()
        if configured.is_absolute():
            return configured.resolve()
        return (self.project_root / configured).resolve()

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            if self.database_url.startswith("sqlite:///") and not self.database_url.startswith("sqlite:////"):
                relative_path = self.database_url.removeprefix("sqlite:///")
                return f"sqlite:///{(self.project_root / relative_path).resolve().as_posix()}"
            return self.database_url
        database_path = self.data_path / "bluetooth_scanner.sqlite3"
        return f"sqlite:///{database_path.as_posix()}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
