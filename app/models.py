"""SQLAlchemy-Modelle für Einstellungen, Status, Messwerte und Alarmhistorie."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    """Aktuelle UTC-Zeit mit Zeitzone (für SQLite-kompatible Defaults)."""
    return datetime.now(timezone.utc)


class Settings(Base):
    """Singleton-Konfiguration, die Weboberfläche und Worker teilen."""

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stream_url: Mapped[str] = mapped_column(String(2048), default="")
    telegram_bot_token: Mapped[str] = mapped_column(String(256), default="")
    telegram_chat_id: Mapped[str] = mapped_column(String(128), default="")
    brightness_threshold: Mapped[float] = mapped_column(Float, default=18.0)
    freeze_threshold: Mapped[float] = mapped_column(Float, default=2.5)
    audio_rms_threshold: Mapped[float] = mapped_column(Float, default=-42.0)
    check_interval_seconds: Mapped[int] = mapped_column(Integer, default=10)
    cooldown_seconds: Mapped[int] = mapped_column(Integer, default=120)
    send_screenshot: Mapped[bool] = mapped_column(Boolean, default=True)
    monitoring_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class Alert(Base):
    """Gespeicherter Alarm (lokal, unabhängig von Telegram)."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    alert_type: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, default="")


class MetricSample(Base):
    """Ein Messpunkt aus einem Prüfzyklus (Helligkeit, Frame-Diff, Audio)."""

    __tablename__ = "metric_samples"
    __table_args__ = (Index("ix_metric_samples_recorded_at", "recorded_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    brightness: Mapped[float | None] = mapped_column(Float, nullable=True)
    frame_diff: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_rms: Mapped[float | None] = mapped_column(Float, nullable=True)


class CheckStatus(Base):
    """Letztes Prüfergebnis des Workers (eine Zeile)."""

    __tablename__ = "check_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[str] = mapped_column(String(32), default="idle")
    error_type: Mapped[str] = mapped_column(String(128), default="")
    message: Mapped[str] = mapped_column(Text, default="Überwachung ist gestoppt.")
    brightness: Mapped[float | None] = mapped_column(Float, nullable=True)
    frame_diff: Mapped[float | None] = mapped_column(Float, nullable=True)
    audio_rms: Mapped[float | None] = mapped_column(Float, nullable=True)
    has_preview: Mapped[bool] = mapped_column(Boolean, default=False)
