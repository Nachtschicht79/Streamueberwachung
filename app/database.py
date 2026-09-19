"""SQLite-Engine, Sessions und Schema-Initialisierung."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PREVIEW_PATH = DATA_DIR / "preview.jpg"
DB_PATH = DATA_DIR / "streamwatch.db"


class Base(DeclarativeBase):
    """Gemeinsame Basisklasse für alle ORM-Modelle."""


def _ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _build_engine():
    """Erzeugt die SQLite-Engine mit WAL für parallelen Web-/Worker-Zugriff."""
    _ensure_data_dir()
    sqlite_engine = create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
    )

    @event.listens_for(sqlite_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return sqlite_engine


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI-Dependency: eine Session pro Request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _linear_audio_to_dbfs(value: float | None, *, zero_to_floor: bool) -> float | None:
    """Alte lineare RMS-Werte (0–1) nach dBFS wandeln; negative Werte bleiben."""
    from monitor.stream import DBFS_FLOOR, rms_to_dbfs

    if value is None:
        return None
    if value > 0:
        return round(rms_to_dbfs(value) * 2) / 2
    if value == 0 and zero_to_floor:
        return DBFS_FLOOR
    if zero_to_floor and value < 0:
        return round(value * 2) / 2
    return value


def init_db() -> None:
    """Legt Tabellen an und schreibt Default-Zeilen, falls noch keine existieren."""
    from app.models import CheckStatus, Settings

    _ensure_data_dir()
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        if settings is None:
            db.add(Settings())
        else:
            converted = _linear_audio_to_dbfs(settings.audio_rms_threshold, zero_to_floor=True)
            if converted is not None and converted != settings.audio_rms_threshold:
                settings.audio_rms_threshold = converted
        status = db.query(CheckStatus).first()
        if status is None:
            db.add(CheckStatus())
        else:
            converted_rms = _linear_audio_to_dbfs(status.audio_rms, zero_to_floor=False)
            if converted_rms != status.audio_rms:
                status.audio_rms = converted_rms
        db.commit()
    finally:
        db.close()
