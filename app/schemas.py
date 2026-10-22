"""Pydantic-Schemas für die REST-API."""

from __future__ import annotations

from datetime import datetime, timezone

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.streams import SOURCE_WEBSTREAM


def ensure_utc(value: datetime | None) -> datetime | None:
    """SQLite liefert Zeiten oft ohne tzinfo; gespeichert wird UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class SettingsUpdate(BaseModel):
    """Vom Dashboard gespeicherte Einstellungen (ohne Start/Stop-Flag)."""

    stream_url: str = ""
    backup_stream_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    brightness_threshold: float = Field(default=18.0, ge=0, le=255)
    freeze_threshold: float = Field(default=2.5, ge=0)
    audio_rms_threshold: float = Field(default=-42.0, ge=-100, le=0)
    outage_black: bool = True
    outage_freeze: bool = False
    outage_silence: bool = True
    check_interval_seconds: int = Field(default=10, ge=3, le=600)
    cooldown_seconds: int = Field(default=120, ge=0, le=86400)
    send_screenshot: bool = True
    monitor_target: Literal["webstream", "makalu", "both"] = "webstream"


class SettingsOut(SettingsUpdate):
    """Einstellungen inklusive ob die Überwachung gerade läuft."""

    monitoring_enabled: bool = False


class TelegramTestRequest(BaseModel):
    """Optionale Zugangsdaten für den Telegram-Test (sonst Werte aus der DB)."""

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None


class TelegramTestResponse(BaseModel):
    ok: bool
    message: str


class StreamStatusOut(BaseModel):
    """Prüfergebnis eines einzelnen Streams."""

    source: str
    label: str
    last_check_at: datetime | None
    result: str
    error_type: str
    message: str
    brightness: float | None
    frame_diff: float | None
    audio_rms: float | None
    has_preview: bool

    @field_validator("last_check_at", mode="before")
    @classmethod
    def stream_last_check_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)


class OutageOut(BaseModel):
    """Letzter Sendeausfall oder letzte Stille für das Status-Panel."""

    source: str
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: int
    ongoing: bool

    @field_validator("started_at", "ended_at", mode="before")
    @classmethod
    def outage_times_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)


class StatusOut(BaseModel):
    monitoring_enabled: bool
    monitor_target: str = "webstream"
    last_check_at: datetime | None
    result: str
    error_type: str
    message: str
    brightness: float | None
    frame_diff: float | None
    audio_rms: float | None
    has_preview: bool
    streams: list[StreamStatusOut] = Field(default_factory=list)
    last_outage: OutageOut | None = None
    last_silence: OutageOut | None = None

    @field_validator("last_check_at", mode="before")
    @classmethod
    def last_check_utc(cls, value: datetime | None) -> datetime | None:
        return ensure_utc(value)


class MetricSampleOut(BaseModel):
    recorded_at: datetime
    source: str = SOURCE_WEBSTREAM
    brightness: float | None
    frame_diff: float | None
    audio_rms: float | None

    model_config = {"from_attributes": True}

    @field_validator("source", mode="before")
    @classmethod
    def metric_source(cls, value: str | None) -> str:
        return value or SOURCE_WEBSTREAM

    @field_validator("recorded_at", mode="before")
    @classmethod
    def recorded_at_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value) or value.replace(tzinfo=timezone.utc)


class AlertOut(BaseModel):
    id: int
    created_at: datetime
    alert_type: str
    source: str = SOURCE_WEBSTREAM
    message: str

    model_config = {"from_attributes": True}

    @field_validator("source", mode="before")
    @classmethod
    def alert_source(cls, value: str | None) -> str:
        return value or SOURCE_WEBSTREAM

    @field_validator("created_at", mode="before")
    @classmethod
    def created_at_utc(cls, value: datetime) -> datetime:
        return ensure_utc(value) or value.replace(tzinfo=timezone.utc)


class SimpleOk(BaseModel):
    ok: bool
    message: str
