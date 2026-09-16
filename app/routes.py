"""HTTP-Routen: Dashboard, Einstellungen, Steuerung und Status."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import BASE_DIR, PREVIEW_PATH, get_db
from app.models import Alert, CheckStatus, Settings
from app.schemas import (
    AlertOut,
    SettingsOut,
    SettingsUpdate,
    SimpleOk,
    StatusOut,
    TelegramTestRequest,
    TelegramTestResponse,
)
from monitor.telegram import send_test

router = APIRouter()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def _settings_row(db: Session) -> Settings:
    row = db.query(Settings).first()
    if row is None:
        row = Settings()
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _status_row(db: Session) -> CheckStatus:
    row = db.query(CheckStatus).first()
    if row is None:
        row = CheckStatus()
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _to_settings_out(row: Settings) -> SettingsOut:
    return SettingsOut(
        stream_url=row.stream_url,
        telegram_bot_token=row.telegram_bot_token,
        telegram_chat_id=row.telegram_chat_id,
        brightness_threshold=row.brightness_threshold,
        freeze_threshold=row.freeze_threshold,
        audio_rms_threshold=row.audio_rms_threshold,
        check_interval_seconds=row.check_interval_seconds,
        cooldown_seconds=row.cooldown_seconds,
        send_screenshot=row.send_screenshot,
        monitoring_enabled=row.monitoring_enabled,
    )


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


@router.get("/api/settings", response_model=SettingsOut)
def get_settings(db: Session = Depends(get_db)) -> SettingsOut:
    return _to_settings_out(_settings_row(db))


@router.post("/api/settings", response_model=SettingsOut)
def save_settings(payload: SettingsUpdate, db: Session = Depends(get_db)) -> SettingsOut:
    row = _settings_row(db)
    row.stream_url = payload.stream_url.strip()
    row.telegram_bot_token = payload.telegram_bot_token.strip()
    row.telegram_chat_id = payload.telegram_chat_id.strip()
    row.brightness_threshold = payload.brightness_threshold
    row.freeze_threshold = payload.freeze_threshold
    row.audio_rms_threshold = payload.audio_rms_threshold
    row.check_interval_seconds = payload.check_interval_seconds
    row.cooldown_seconds = payload.cooldown_seconds
    row.send_screenshot = payload.send_screenshot
    db.commit()
    db.refresh(row)
    return _to_settings_out(row)


@router.post("/api/monitor/start", response_model=SimpleOk)
def start_monitor(db: Session = Depends(get_db)) -> SimpleOk:
    row = _settings_row(db)
    if not (row.stream_url or "").strip():
        raise HTTPException(status_code=400, detail="Bitte zuerst eine Stream-URL speichern.")
    row.monitoring_enabled = True
    db.commit()
    return SimpleOk(ok=True, message="Überwachung gestartet.")


@router.post("/api/monitor/stop", response_model=SimpleOk)
def stop_monitor(db: Session = Depends(get_db)) -> SimpleOk:
    row = _settings_row(db)
    row.monitoring_enabled = False
    db.commit()
    return SimpleOk(ok=True, message="Überwachung gestoppt.")


@router.post("/api/telegram/test", response_model=TelegramTestResponse)
def test_telegram(
    payload: TelegramTestRequest | None = None,
    db: Session = Depends(get_db),
) -> TelegramTestResponse:
    row = _settings_row(db)
    token = (payload.telegram_bot_token if payload else None) or row.telegram_bot_token
    chat_id = (payload.telegram_chat_id if payload else None) or row.telegram_chat_id
    ok, message = send_test(token or "", chat_id or "")
    return TelegramTestResponse(ok=ok, message=message)


@router.get("/api/status", response_model=StatusOut)
def get_status(db: Session = Depends(get_db)) -> StatusOut:
    settings = _settings_row(db)
    status = _status_row(db)
    return StatusOut(
        monitoring_enabled=settings.monitoring_enabled,
        last_check_at=status.last_check_at,
        result=status.result,
        error_type=status.error_type,
        message=status.message,
        brightness=status.brightness,
        frame_diff=status.frame_diff,
        audio_rms=status.audio_rms,
        has_preview=status.has_preview and PREVIEW_PATH.is_file(),
    )


@router.get("/api/alerts", response_model=list[AlertOut])
def list_alerts(db: Session = Depends(get_db)) -> list[Alert]:
    return (
        db.query(Alert)
        .order_by(Alert.created_at.desc(), Alert.id.desc())
        .limit(50)
        .all()
    )


@router.delete("/api/alerts/{alert_id}", response_model=SimpleOk)
def delete_alert(alert_id: int, db: Session = Depends(get_db)) -> SimpleOk:
    row = db.query(Alert).filter(Alert.id == alert_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Alarm nicht gefunden.")
    db.delete(row)
    db.commit()
    return SimpleOk(ok=True, message="Alarm gelöscht.")


@router.delete("/api/alerts", response_model=SimpleOk)
def delete_all_alerts(db: Session = Depends(get_db)) -> SimpleOk:
    deleted = db.query(Alert).delete(synchronize_session=False)
    db.commit()
    if deleted:
        return SimpleOk(ok=True, message="Alarmhistorie gelöscht.")
    return SimpleOk(ok=True, message="Keine Alarme vorhanden.")


@router.get("/preview.jpg")
def preview_image() -> FileResponse:
    path: Path = PREVIEW_PATH
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Kein Vorschaubild vorhanden.")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, max-age=0"},
    )
