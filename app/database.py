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


def init_db() -> None:
    """Legt Tabellen an und schreibt Default-Zeilen, falls noch keine existieren."""
    from app.models import CheckStatus, Settings

    _ensure_data_dir()
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        if db.query(Settings).first() is None:
            db.add(Settings())
        if db.query(CheckStatus).first() is None:
            db.add(CheckStatus())
        db.commit()
    finally:
        db.close()
