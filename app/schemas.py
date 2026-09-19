"""Pydantic-Schemas für die REST-API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class SettingsUpdate(BaseModel):
    """Vom Dashboard gespeicherte Einstellungen (ohne Start/Stop-Flag)."""

    stream_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    brightness_threshold: float = Field(default=18.0, ge=0, le=255)
    freeze_threshold: float = Field(default=2.5, ge=0)
    audio_rms_threshold: float = Field(default=-42.0, ge=-100, le=0)
    check_interval_seconds: int = Field(default=10, ge=3, le=600)
    cooldown_seconds: int = Field(default=120, ge=0, le=86400)
    send_screenshot: bool = True


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


class StatusOut(BaseModel):
    monitoring_enabled: bool
    last_check_at: datetime | None
    result: str
    error_type: str
    message: str
    brightness: float | None
    frame_diff: float | None
    audio_rms: float | None
    has_preview: bool


class AlertOut(BaseModel):
    id: int
    created_at: datetime
    alert_type: str
    message: str

    model_config = {"from_attributes": True}


class SimpleOk(BaseModel):
    ok: bool
    message: str
