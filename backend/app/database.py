from collections.abc import Generator

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
SQLITE_BUSY_TIMEOUT_SECONDS = 30
database_url = settings.resolved_database_url


def ensure_sqlite_directory(url: str) -> None:
    if not url.startswith("sqlite"):
        return
    database_path = make_url(url).database
    if database_path and database_path != ":memory:":
        Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)


def engine_options(database_url: str) -> dict[str, object]:
    """Use one waitable connection for the single-file SQLite local runner."""
    if not database_url.startswith("sqlite"):
        return {"pool_pre_ping": True, "future": True}
    return {
        "connect_args": {
            "check_same_thread": False,
            "timeout": SQLITE_BUSY_TIMEOUT_SECONDS,
        },
        "pool_size": 1,
        "max_overflow": 0,
        "pool_timeout": SQLITE_BUSY_TIMEOUT_SECONDS,
        "future": True,
    }


ensure_sqlite_directory(database_url)
engine = create_engine(database_url, **engine_options(database_url))


if database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def configure_sqlite_connection(connection: object, _: object) -> None:
        cursor = connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_SECONDS * 1000}")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
