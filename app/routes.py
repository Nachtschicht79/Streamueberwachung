"""HTTP-Routen: Dashboard, Einstellungen, Steuerung und Status."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import BASE_DIR, get_db, preview_path_for
from app.hls_proxy import proxy_hls
from app.models import OUTAGE_BROADCAST, OUTAGE_SILENCE, Alert, CheckStatus, MetricSample, Outage, Settings
from app.schemas import (
    AlertOut,
    MetricSampleOut,
    OutageOut,
    SettingsOut,
    SettingsUpdate,
    SimpleOk,
    StatusOut,
    StreamStatusOut,
    TelegramTestRequest,
    TelegramTestResponse,
    ensure_utc,
)
from app.streams import (
    SOURCE_MAKALU,
    SOURCE_WEBSTREAM,
    normalize_monitor_target,
    normalize_source,
    selected_sources,
    source_label,
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


def _latest_episode(db: Session, kind: str) -> OutageOut | None:
    """Laufender Zeitraum hat Vorrang, sonst der zuletzt beendete."""
    open_row = (
        db.query(Outage)
        .filter(Outage.kind == kind, Outage.ended_at.is_(None))
        .order_by(Outage.started_at.desc(), Outage.id.desc())
        .first()
    )
    row = open_row
    if row is None:
        row = (
            db.query(Outage)
            .filter(Outage.kind == kind, Outage.ended_at.isnot(None))
            .order_by(Outage.ended_at.desc(), Outage.id.desc())
            .first()
        )
    if row is None:
        return None
    started = ensure_utc(row.started_at)
    if started is None:
        return None
    ended = ensure_utc(row.ended_at)
    ongoing = ended is None
    end_point = datetime.now(timezone.utc) if ongoing else ended
    duration = max(0, int((end_point - started).total_seconds()))
    return OutageOut(
        source=row.source or SOURCE_WEBSTREAM,
        started_at=started,
        ended_at=ended,
        duration_seconds=duration,
        ongoing=ongoing,
    )


def _to_settings_out(row: Settings) -> SettingsOut:
    return SettingsOut(
        stream_url=row.stream_url,
        backup_stream_url=getattr(row, "backup_stream_url", "") or "",
        telegram_bot_token=row.telegram_bot_token,
        telegram_chat_id=row.telegram_chat_id,
        brightness_threshold=row.brightness_threshold,
        freeze_threshold=row.freeze_threshold,
        audio_rms_threshold=row.audio_rms_threshold,
        outage_black=bool(row.outage_black),
        outage_freeze=bool(row.outage_freeze),
        outage_silence=bool(row.outage_silence),
        check_interval_seconds=row.check_interval_seconds,
        cooldown_seconds=row.cooldown_seconds,
        send_screenshot=row.send_screenshot,
        monitoring_enabled=row.monitoring_enabled,
        monitor_target=normalize_monitor_target(getattr(row, "monitor_target", None)),
    )


def _stream_url(row: Settings, source: str) -> str:
    if source == SOURCE_MAKALU:
        return (getattr(row, "backup_stream_url", "") or "").strip()
    return (row.stream_url or "").strip()


def _configured_sources(row: Settings) -> list[str]:
    """Gewählte Quellen, für die auch eine URL hinterlegt ist."""
    return [
        source
        for source in selected_sources(getattr(row, "monitor_target", None))
        if _stream_url(row, source)
    ]


def _status_row_for(db: Session, source: str) -> CheckStatus | None:
    return (
        db.query(CheckStatus)
        .filter(CheckStatus.source == normalize_source(source))
        .first()
    )


def _to_stream_status(row: CheckStatus | None, source: str, url: str = "") -> StreamStatusOut:
    normalized = normalize_source(source)
    if row is None:
        message = "Noch keine Prüfung."
        if not url.strip():
            message = f"Keine {source_label(normalized)}-URL gesetzt."
        return StreamStatusOut(
            source=normalized,
            label=source_label(normalized),
            last_check_at=None,
            result="idle",
            error_type="",
            message=message,
            brightness=None,
            frame_diff=None,
            audio_rms=None,
            has_preview=False,
        )
    return StreamStatusOut(
        source=normalized,
        label=source_label(normalized),
        last_check_at=row.last_check_at,
        result=row.result,
        error_type=row.error_type,
        message=row.message,
        brightness=row.brightness,
        frame_diff=row.frame_diff,
        audio_rms=row.audio_rms,
        has_preview=bool(row.has_preview) and preview_path_for(normalized).is_file(),
    )


def _start_error(row: Settings) -> str:
    target = normalize_monitor_target(getattr(row, "monitor_target", None))
    if target == SOURCE_MAKALU:
        return "Bitte zuerst eine Makalu-URL speichern."
    if target == "both":
        return "Bitte zuerst eine Webstream- oder Makalu-URL speichern."
    return "Bitte zuerst eine Webstream-URL speichern."


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
    row.backup_stream_url = payload.backup_stream_url.strip()
    row.telegram_bot_token = payload.telegram_bot_token.strip()
    row.telegram_chat_id = payload.telegram_chat_id.strip()
    row.brightness_threshold = payload.brightness_threshold
    row.freeze_threshold = payload.freeze_threshold
    row.audio_rms_threshold = payload.audio_rms_threshold
    row.outage_black = payload.outage_black
    row.outage_freeze = payload.outage_freeze
    row.outage_silence = payload.outage_silence
    row.check_interval_seconds = payload.check_interval_seconds
    row.cooldown_seconds = payload.cooldown_seconds
    row.send_screenshot = payload.send_screenshot
    row.monitor_target = payload.monitor_target
    db.commit()
    db.refresh(row)
    return _to_settings_out(row)


@router.post("/api/monitor/start", response_model=SimpleOk)
def start_monitor(db: Session = Depends(get_db)) -> SimpleOk:
    row = _settings_row(db)
    if not _configured_sources(row):
        raise HTTPException(status_code=400, detail=_start_error(row))
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
    sources = selected_sources(getattr(settings, "monitor_target", None))
    streams = [
        _to_stream_status(_status_row_for(db, source), source, _stream_url(settings, source))
        for source in sources
    ]
    latest = max(
        (item for item in streams if item.last_check_at is not None),
        key=lambda item: item.last_check_at or datetime.min.replace(tzinfo=timezone.utc),
        default=None,
    )
    errored = next((item for item in streams if item.result == "error"), None)
    headline = latest or errored or (streams[0] if streams else None)
    if len(streams) > 1:
        message = " ".join(f"{item.label}: {item.message}" for item in streams)
    else:
        message = headline.message if headline else "Noch keine Prüfung."
    error_types = []
    for item in streams:
        for part in (item.error_type or "").split(","):
            cleaned = part.strip()
            if cleaned and cleaned not in error_types:
                error_types.append(cleaned)
    if any(item.result == "error" for item in streams):
        result = "error"
    elif any(item.result == "ok" for item in streams):
        result = "ok"
    else:
        result = headline.result if headline else "idle"
    return StatusOut(
        monitoring_enabled=settings.monitoring_enabled,
        monitor_target=normalize_monitor_target(getattr(settings, "monitor_target", None)),
        last_check_at=latest.last_check_at if latest else None,
        result=result,
        error_type=",".join(error_types),
        message=message,
        brightness=headline.brightness if headline else None,
        frame_diff=headline.frame_diff if headline else None,
        audio_rms=headline.audio_rms if headline else None,
        has_preview=any(item.has_preview for item in streams),
        streams=streams,
        last_outage=_latest_episode(db, OUTAGE_BROADCAST),
        last_silence=_latest_episode(db, OUTAGE_SILENCE),
    )


@router.get("/api/metrics", response_model=list[MetricSampleOut])
def list_metrics(
    minutes: int = Query(default=120, ge=5, le=360),
    db: Session = Depends(get_db),
) -> list[MetricSample]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return (
        db.query(MetricSample)
        .filter(MetricSample.recorded_at >= cutoff)
        .order_by(MetricSample.recorded_at.asc(), MetricSample.id.asc())
        .all()
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


@router.get("/api/hls/proxy")
@router.get("/api/hls/proxy/{name}")
def hls_proxy(
    url: str = Query(default=""),
    name: str | None = None,
    db: Session = Depends(get_db),
) -> Response:
    _ = name
    return proxy_hls(url, _settings_row(db))


@router.get("/preview.jpg")
def preview_image(source: str = Query(default=SOURCE_WEBSTREAM)) -> FileResponse:
    path = preview_path_for(source)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Kein Vorschaubild vorhanden.")
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, max-age=0"},
    )
