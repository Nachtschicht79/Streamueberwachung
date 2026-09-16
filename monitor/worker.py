"""Eigenständiger Worker-Prozess: prüft den Stream und schreibt Status in SQLite."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal, init_db
from app.database import PREVIEW_PATH
from app.models import Alert, CheckStatus, Settings
from monitor.detector import (
    ALERT_LABELS,
    ALERT_OFFLINE,
    CheckMetrics,
    analyze_video,
    apply_silence,
    summarize,
)
from monitor.stream import StreamCapture, measure_audio_rms, save_preview
from monitor.telegram import send_message, send_photo

logger = logging.getLogger(__name__)

CONSECUTIVE_NEEDED = 2
ALERT_TYPES = ("black", "freeze", "silence", "offline")


@dataclass
class MonitorConfig:
    """Snapshot der Settings, damit der Stream-Check die DB nicht lange blockiert."""

    stream_url: str
    telegram_bot_token: str
    telegram_chat_id: str
    brightness_threshold: float
    freeze_threshold: float
    audio_rms_threshold: float
    check_interval_seconds: int
    cooldown_seconds: int
    send_screenshot: bool
    monitoring_enabled: bool


def run_worker() -> None:
    """Endlosschleife – wird als eigener Prozess aus main.py gestartet."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [worker] %(levelname)s %(message)s",
    )
    init_db()
    logger.info("Monitor-Worker gestartet.")

    capture = StreamCapture()
    previous_gray = None
    last_url = ""
    consecutive: dict[str, int] = {key: 0 for key in ALERT_TYPES}
    last_alert_at: dict[str, float] = {}
    was_enabled = False

    while True:
        interval = 1.0
        try:
            config = _load_config()
            if config is None:
                time.sleep(1)
                continue

            if not config.monitoring_enabled:
                if was_enabled:
                    capture.release()
                    previous_gray = None
                    consecutive = {key: 0 for key in ALERT_TYPES}
                    _mark_idle()
                    logger.info("Überwachung gestoppt.")
                was_enabled = False
                _interruptible_sleep(0.8)
                continue

            was_enabled = True
            interval = float(max(3, config.check_interval_seconds))

            if config.stream_url != last_url:
                capture.release()
                previous_gray = None
                consecutive = {key: 0 for key in ALERT_TYPES}
                last_url = config.stream_url

            previous_gray, consecutive = _run_check(
                config=config,
                capture=capture,
                previous_gray=previous_gray,
                consecutive=consecutive,
                last_alert_at=last_alert_at,
            )
        except Exception:
            logger.exception("Unerwarteter Worker-Fehler")
            try:
                capture.release()
            except Exception:
                pass
            previous_gray = None

        _interruptible_sleep(interval)


def _load_config() -> MonitorConfig | None:
    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        if settings is None:
            return None
        return MonitorConfig(
            stream_url=settings.stream_url or "",
            telegram_bot_token=settings.telegram_bot_token or "",
            telegram_chat_id=settings.telegram_chat_id or "",
            brightness_threshold=float(settings.brightness_threshold),
            freeze_threshold=float(settings.freeze_threshold),
            audio_rms_threshold=float(settings.audio_rms_threshold),
            check_interval_seconds=int(settings.check_interval_seconds),
            cooldown_seconds=int(settings.cooldown_seconds),
            send_screenshot=bool(settings.send_screenshot),
            monitoring_enabled=bool(settings.monitoring_enabled),
        )
    finally:
        db.close()


def _run_check(
    config: MonitorConfig,
    capture: StreamCapture,
    previous_gray,
    consecutive: dict[str, int],
    last_alert_at: dict[str, float],
) -> tuple[object, dict[str, int]]:
    url = config.stream_url.strip()
    has_preview = PREVIEW_PATH.is_file()

    if not url:
        metrics = CheckMetrics(issues=[], message="Keine Stream-URL gesetzt.")
        _store_status(metrics, has_preview=has_preview, result="error")
        return previous_gray, consecutive

    frame = capture.get_frame(url)
    if frame is None:
        consecutive[ALERT_OFFLINE] = consecutive.get(ALERT_OFFLINE, 0) + 1
        for key in ALERT_TYPES:
            if key != ALERT_OFFLINE:
                consecutive[key] = 0
        metrics = CheckMetrics(issues=[ALERT_OFFLINE])
        metrics.message = summarize(metrics)
        _store_status(metrics, has_preview=has_preview, result="error")
        _maybe_alert(config, [ALERT_OFFLINE], consecutive, last_alert_at, metrics.message)
        return None, consecutive

    has_preview = save_preview(frame, PREVIEW_PATH) or has_preview
    metrics = analyze_video(
        frame,
        previous_gray,
        config.brightness_threshold,
        config.freeze_threshold,
    )
    audio_rms, audio_note = measure_audio_rms(url)
    apply_silence(metrics, audio_rms, config.audio_rms_threshold)
    metrics.message = summarize(metrics, audio_note=audio_note)

    for key in ALERT_TYPES:
        if key in metrics.issues:
            consecutive[key] = consecutive.get(key, 0) + 1
        else:
            consecutive[key] = 0

    result = "error" if metrics.issues else "ok"
    _store_status(metrics, has_preview=has_preview, result=result)
    _maybe_alert(config, metrics.issues, consecutive, last_alert_at, metrics.message)
    return metrics.gray, consecutive


def _maybe_alert(
    config: MonitorConfig,
    issues: list[str],
    consecutive: dict[str, int],
    last_alert_at: dict[str, float],
    status_message: str,
) -> None:
    """Sendet Telegram erst nach zwei gleichen Fehlern und respektiert den Cooldown."""
    now = time.monotonic()
    fired: list[str] = []
    for issue in issues:
        if consecutive.get(issue, 0) < CONSECUTIVE_NEEDED:
            continue
        last = last_alert_at.get(issue, 0.0)
        if now - last < max(0, config.cooldown_seconds):
            continue
        last_alert_at[issue] = now
        fired.append(issue)

    if not fired:
        return

    labels = ", ".join(ALERT_LABELS.get(item, item) for item in fired)
    text = f"Streamüberwachung: {labels}\n{status_message}"
    _save_alerts(fired, text)

    token = config.telegram_bot_token.strip()
    chat_id = config.telegram_chat_id.strip()
    if not token or not chat_id:
        logger.info("Alarm gespeichert, Telegram nicht konfiguriert.")
        return

    if config.send_screenshot and PREVIEW_PATH.is_file() and ALERT_OFFLINE not in fired:
        ok, hint = send_photo(token, chat_id, PREVIEW_PATH, text)
    else:
        ok, hint = send_message(token, chat_id, text)

    if ok:
        logger.info("Telegram-Alarm gesendet (%s).", labels)
    else:
        logger.warning("Telegram-Versand fehlgeschlagen: %s", hint)


def _save_alerts(types: list[str], message: str) -> None:
    db = SessionLocal()
    try:
        for alert_type in types:
            db.add(
                Alert(
                    created_at=datetime.now(timezone.utc),
                    alert_type=alert_type,
                    message=message,
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Alarm konnte nicht gespeichert werden.")
    finally:
        db.close()


def _store_status(metrics: CheckMetrics, has_preview: bool, result: str) -> None:
    db = SessionLocal()
    try:
        status = db.query(CheckStatus).first()
        if status is None:
            status = CheckStatus()
            db.add(status)
        status.last_check_at = datetime.now(timezone.utc)
        status.result = result
        status.error_type = ",".join(metrics.issues)
        status.message = metrics.message
        status.brightness = metrics.brightness
        status.frame_diff = metrics.frame_diff
        status.audio_rms = metrics.audio_rms
        status.has_preview = has_preview
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Status konnte nicht gespeichert werden.")
    finally:
        db.close()


def _mark_idle() -> None:
    db = SessionLocal()
    try:
        status = db.query(CheckStatus).first()
        if status is None:
            return
        status.result = "idle"
        if not status.last_check_at:
            status.message = "Überwachung ist gestoppt."
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _interruptible_sleep(seconds: float) -> None:
    """Kurze Schlafschnitte, damit Start/Stop nicht bis zum vollen Intervall warten."""
    deadline = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if _monitoring_enabled():
            time.sleep(min(0.5, remaining))
        else:
            time.sleep(min(0.4, remaining))
            if not _monitoring_enabled():
                break


def _monitoring_enabled() -> bool:
    db: Session | None = None
    try:
        db = SessionLocal()
        settings = db.query(Settings).first()
        return bool(settings and settings.monitoring_enabled)
    except Exception:
        return True
    finally:
        if db is not None:
            db.close()
