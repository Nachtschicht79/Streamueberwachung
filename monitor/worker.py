"""Eigenständiger Worker-Prozess: prüft den Stream und schreibt Status in SQLite."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.database import SessionLocal, init_db, preview_path_for
from app.models import (
    OUTAGE_BROADCAST,
    OUTAGE_SILENCE,
    Alert,
    CheckStatus,
    MetricSample,
    Outage,
    Settings,
)
from app.streams import (
    SOURCE_MAKALU,
    SOURCE_WEBSTREAM,
    normalize_monitor_target,
    selected_sources,
    source_label,
)
from monitor.detector import (
    ALERT_BLACK,
    ALERT_BROADCAST,
    ALERT_FREEZE,
    ALERT_LABELS,
    ALERT_OFFLINE,
    ALERT_SILENCE,
    CheckMetrics,
    analyze_video,
    apply_silence,
    combination_met,
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
    backup_stream_url: str
    telegram_bot_token: str
    telegram_chat_id: str
    brightness_threshold: float
    freeze_threshold: float
    audio_rms_threshold: float
    outage_black: bool
    outage_freeze: bool
    outage_silence: bool
    check_interval_seconds: int
    cooldown_seconds: int
    send_screenshot: bool
    monitoring_enabled: bool
    monitor_target: str


@dataclass
class StreamRuntime:
    """Laufzeitstatus eines einzelnen überwachten Streams."""

    source: str
    capture: StreamCapture
    previous_gray: object | None = None
    consecutive: dict[str, int] = field(default_factory=dict)
    last_url: str = ""
    last_alert_at: dict[str, float] = field(default_factory=dict)
    broadcast_streak: int = 0
    broadcast_pending_since: datetime | None = None
    broadcast_open: bool = False
    silence_pending_since: datetime | None = None
    silence_open: bool = False

    def __post_init__(self) -> None:
        if not self.consecutive:
            self.consecutive = _blank_consecutive()

    def reset(self) -> None:
        self.capture.release()
        self.previous_gray = None
        self.consecutive = _blank_consecutive()
        self.last_url = ""
        self.last_alert_at = {}
        self.broadcast_streak = 0
        self.broadcast_pending_since = None
        self.broadcast_open = False
        self.silence_pending_since = None
        self.silence_open = False


def run_worker() -> None:
    """Endlosschleife – wird als eigener Prozess aus main.py gestartet."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [worker] %(levelname)s %(message)s",
    )
    init_db()
    logger.info("Monitor-Worker gestartet.")

    runtimes = {
        SOURCE_WEBSTREAM: StreamRuntime(source=SOURCE_WEBSTREAM, capture=StreamCapture()),
        SOURCE_MAKALU: StreamRuntime(source=SOURCE_MAKALU, capture=StreamCapture()),
    }
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
                    for runtime in runtimes.values():
                        runtime.reset()
                    _mark_idle()
                    logger.info("Überwachung gestoppt.")
                was_enabled = False
                _interruptible_sleep(0.8)
                continue

            was_enabled = True
            interval = float(max(3, config.check_interval_seconds))
            active_sources = _active_sources(config)
            _close_inactive_outages(active_sources)
            for source, runtime in runtimes.items():
                if source not in active_sources and runtime.last_url:
                    runtime.reset()

            for source in active_sources:
                url = _url_for(config, source)
                runtime = runtimes[source]
                try:
                    if url != runtime.last_url:
                        if runtime.last_url:
                            _close_open_outages(runtime.source)
                        runtime.reset()
                        runtime.last_url = url
                    runtime.previous_gray, runtime.consecutive = _run_check(
                        config=config,
                        runtime=runtime,
                        url=url,
                    )
                except Exception:
                    logger.exception("Prüfung fehlgeschlagen (%s).", source_label(source))
                    try:
                        runtime.capture.release()
                    except Exception:
                        pass
                    runtime.previous_gray = None
        except Exception:
            logger.exception("Unerwarteter Worker-Fehler")
            for runtime in runtimes.values():
                try:
                    runtime.reset()
                except Exception:
                    pass

        _interruptible_sleep(interval)


def _load_config() -> MonitorConfig | None:
    db = SessionLocal()
    try:
        settings = db.query(Settings).first()
        if settings is None:
            return None
        return MonitorConfig(
            stream_url=settings.stream_url or "",
            backup_stream_url=getattr(settings, "backup_stream_url", "") or "",
            telegram_bot_token=settings.telegram_bot_token or "",
            telegram_chat_id=settings.telegram_chat_id or "",
            brightness_threshold=float(settings.brightness_threshold),
            freeze_threshold=float(settings.freeze_threshold),
            audio_rms_threshold=float(settings.audio_rms_threshold),
            outage_black=bool(settings.outage_black),
            outage_freeze=bool(settings.outage_freeze),
            outage_silence=bool(settings.outage_silence),
            check_interval_seconds=int(settings.check_interval_seconds),
            cooldown_seconds=int(settings.cooldown_seconds),
            send_screenshot=bool(settings.send_screenshot),
            monitoring_enabled=bool(settings.monitoring_enabled),
            monitor_target=normalize_monitor_target(getattr(settings, "monitor_target", None)),
        )
    finally:
        db.close()


def _url_for(config: MonitorConfig, source: str) -> str:
    if source == SOURCE_MAKALU:
        return config.backup_stream_url.strip()
    return config.stream_url.strip()


def _active_sources(config: MonitorConfig) -> list[str]:
    return [source for source in selected_sources(config.monitor_target) if _url_for(config, source)]


def _run_check(
    config: MonitorConfig,
    runtime: StreamRuntime,
    url: str,
) -> tuple[object, dict[str, int]]:
    source = runtime.source
    preview = preview_path_for(source)
    has_preview = preview.is_file()
    label = source_label(source)

    if not url:
        metrics = CheckMetrics(issues=[], message=f"Keine {label}-URL gesetzt.")
        _store_status(source, metrics, has_preview=has_preview, result="error")
        return runtime.previous_gray, runtime.consecutive

    frame = runtime.capture.get_frame(url)
    if frame is None:
        now = datetime.now(timezone.utc)
        runtime.consecutive[ALERT_OFFLINE] = runtime.consecutive.get(ALERT_OFFLINE, 0) + 1
        for key in ALERT_TYPES:
            if key != ALERT_OFFLINE:
                runtime.consecutive[key] = 0
        metrics = CheckMetrics(issues=[ALERT_OFFLINE])
        metrics.message = summarize(metrics)
        runtime.consecutive[ALERT_BROADCAST] = 0
        _store_status(source, metrics, has_preview=has_preview, result="error")
        _maybe_alert(config, source, [ALERT_OFFLINE], runtime, metrics.message, preview)
        _track_broadcast(runtime, active=True, now=now)
        _pause_silence(runtime)
        return None, runtime.consecutive

    has_preview = save_preview(frame, preview) or has_preview
    metrics = analyze_video(
        frame,
        runtime.previous_gray,
        config.brightness_threshold,
        config.freeze_threshold,
    )
    audio_rms, audio_note = measure_audio_rms(url)
    apply_silence(metrics, audio_rms, config.audio_rms_threshold)
    metrics.message = summarize(metrics, audio_note=audio_note)

    now = datetime.now(timezone.utc)
    for key in ALERT_TYPES:
        if key == ALERT_SILENCE:
            continue
        if key in metrics.issues:
            runtime.consecutive[key] = runtime.consecutive.get(key, 0) + 1
        else:
            runtime.consecutive[key] = 0

    combination = combination_met(metrics.issues, _selected_criteria(config))
    if combination:
        runtime.consecutive[ALERT_BROADCAST] = runtime.consecutive.get(ALERT_BROADCAST, 0) + 1
    else:
        runtime.consecutive[ALERT_BROADCAST] = 0
    _track_broadcast(runtime, active=combination or ALERT_OFFLINE in metrics.issues, now=now)
    if audio_rms is not None:
        if ALERT_SILENCE in metrics.issues:
            runtime.consecutive[ALERT_SILENCE] = runtime.consecutive.get(ALERT_SILENCE, 0) + 1
        else:
            runtime.consecutive[ALERT_SILENCE] = 0
        _track_silence(runtime, active=ALERT_SILENCE in metrics.issues, now=now)

    result = "error" if metrics.issues else "ok"
    _store_status(source, metrics, has_preview=has_preview, result=result)
    _maybe_alert(
        config,
        source,
        [ALERT_BROADCAST] if combination else [],
        runtime,
        metrics.message,
        preview,
    )
    return metrics.gray, runtime.consecutive


def _blank_consecutive() -> dict[str, int]:
    counts = {key: 0 for key in ALERT_TYPES}
    counts[ALERT_BROADCAST] = 0
    return counts


def _selected_criteria(config: MonitorConfig) -> set[str]:
    """Kriterien, die gemeinsam einen Sendeausfall ergeben."""
    selected: set[str] = set()
    if config.outage_black:
        selected.add(ALERT_BLACK)
    if config.outage_freeze:
        selected.add(ALERT_FREEZE)
    if config.outage_silence:
        selected.add(ALERT_SILENCE)
    return selected


def _maybe_alert(
    config: MonitorConfig,
    source: str,
    issues: list[str],
    runtime: StreamRuntime,
    status_message: str,
    preview,
) -> None:
    """Sendet Telegram erst nach zwei gleichen Fehlern und respektiert den Cooldown."""
    now = time.monotonic()
    fired: list[str] = []
    for issue in issues:
        if runtime.consecutive.get(issue, 0) < CONSECUTIVE_NEEDED:
            continue
        last = runtime.last_alert_at.get(issue, 0.0)
        if now - last < max(0, config.cooldown_seconds):
            continue
        runtime.last_alert_at[issue] = now
        fired.append(issue)

    if not fired:
        return

    labels = ", ".join(ALERT_LABELS.get(item, item) for item in fired)
    text = f"Streamüberwachung ({source_label(source)}): {labels}\n{status_message}"
    _save_alerts(source, fired, text)

    token = config.telegram_bot_token.strip()
    chat_id = config.telegram_chat_id.strip()
    if not token or not chat_id:
        logger.info("Alarm gespeichert, Telegram nicht konfiguriert.")
        return

    if config.send_screenshot and preview.is_file() and ALERT_OFFLINE not in fired:
        ok, hint = send_photo(token, chat_id, preview, text)
    else:
        ok, hint = send_message(token, chat_id, text)

    if ok:
        logger.info("Telegram-Alarm gesendet (%s, %s).", source_label(source), labels)
    else:
        logger.warning("Telegram-Versand fehlgeschlagen: %s", hint)


def _save_alerts(source: str, types: list[str], message: str) -> None:
    db = SessionLocal()
    try:
        for alert_type in types:
            db.add(
                Alert(
                    created_at=datetime.now(timezone.utc),
                    alert_type=alert_type,
                    source=source,
                    message=message,
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Alarm konnte nicht gespeichert werden.")
    finally:
        db.close()


def _store_status(source: str, metrics: CheckMetrics, has_preview: bool, result: str) -> None:
    db = SessionLocal()
    try:
        status = db.query(CheckStatus).filter(CheckStatus.source == source).first()
        if status is None:
            status = CheckStatus(source=source)
            db.add(status)
        now = datetime.now(timezone.utc)
        status.last_check_at = now
        status.result = result
        status.error_type = ",".join(metrics.issues)
        status.message = metrics.message
        status.brightness = metrics.brightness
        status.frame_diff = metrics.frame_diff
        status.audio_rms = metrics.audio_rms
        status.has_preview = has_preview
        db.add(
            MetricSample(
                recorded_at=now,
                source=source,
                brightness=metrics.brightness,
                frame_diff=metrics.frame_diff,
                audio_rms=metrics.audio_rms,
            )
        )
        cutoff = now - timedelta(hours=6)
        db.query(MetricSample).filter(MetricSample.recorded_at < cutoff).delete(
            synchronize_session=False
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Status konnte nicht gespeichert werden.")
    finally:
        db.close()


def _track_broadcast(runtime: StreamRuntime, active: bool, now: datetime) -> None:
    """Sendeausfall ab dem ersten Fehler, gespeichert nach zwei Prüfungen."""
    if active:
        if runtime.broadcast_pending_since is None:
            runtime.broadcast_pending_since = now
        runtime.broadcast_streak += 1
        if not runtime.broadcast_open and runtime.broadcast_streak >= CONSECUTIVE_NEEDED:
            started = runtime.broadcast_pending_since or now
            if _open_outage(runtime.source, OUTAGE_BROADCAST, started):
                runtime.broadcast_open = True
        return
    runtime.broadcast_streak = 0
    runtime.broadcast_pending_since = None
    runtime.broadcast_open = False
    _close_outage(runtime.source, OUTAGE_BROADCAST, now)


def _track_silence(runtime: StreamRuntime, active: bool, now: datetime) -> None:
    """Stille unabhängig vom Sendeausfall, ebenfalls erst nach zwei Prüfungen."""
    if active:
        if runtime.silence_pending_since is None:
            runtime.silence_pending_since = now
        if not runtime.silence_open and runtime.consecutive.get(ALERT_SILENCE, 0) >= CONSECUTIVE_NEEDED:
            started = runtime.silence_pending_since or now
            if _open_outage(runtime.source, OUTAGE_SILENCE, started):
                runtime.silence_open = True
        return
    runtime.silence_pending_since = None
    runtime.silence_open = False
    _close_outage(runtime.source, OUTAGE_SILENCE, now)


def _pause_silence(runtime: StreamRuntime) -> None:
    """Stream weg: Stille weder öffnen noch schließen. Unbestätigten Start verwerfen."""
    if not runtime.silence_open:
        runtime.silence_pending_since = None


def _open_outage(source: str, kind: str, started_at: datetime) -> bool:
    db = SessionLocal()
    try:
        existing = (
            db.query(Outage)
            .filter(Outage.source == source, Outage.kind == kind, Outage.ended_at.is_(None))
            .first()
        )
        if existing is not None:
            return True
        db.add(Outage(source=source, kind=kind, started_at=started_at))
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.exception("Ausfallzeitraum konnte nicht gespeichert werden.")
        return False
    finally:
        db.close()


def _close_outage(source: str, kind: str, ended_at: datetime) -> None:
    db = SessionLocal()
    try:
        rows = (
            db.query(Outage)
            .filter(Outage.source == source, Outage.kind == kind, Outage.ended_at.is_(None))
            .all()
        )
        if not rows:
            return
        for row in rows:
            row.ended_at = ended_at
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Ausfallzeitraum konnte nicht abgeschlossen werden.")
    finally:
        db.close()


def _close_open_outages(source: str | None = None) -> None:
    """Schließt offene Zeiträume. Ohne Quelle alle, sonst nur diese Quelle."""
    db = SessionLocal()
    try:
        query = db.query(Outage).filter(Outage.ended_at.is_(None))
        if source is not None:
            query = query.filter(Outage.source == source)
        rows = query.all()
        if not rows:
            return
        now = datetime.now(timezone.utc)
        for row in rows:
            row.ended_at = now
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Ausfallzeitraum konnte nicht abgeschlossen werden.")
    finally:
        db.close()


def _close_inactive_outages(active_sources: list[str]) -> None:
    """Beendet Zeiträume von Quellen, die gerade nicht überwacht werden."""
    db = SessionLocal()
    try:
        query = db.query(Outage).filter(Outage.ended_at.is_(None))
        if active_sources:
            query = query.filter(Outage.source.notin_(active_sources))
        rows = query.all()
        if not rows:
            return
        now = datetime.now(timezone.utc)
        for row in rows:
            row.ended_at = now
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Ausfallzeitraum konnte nicht abgeschlossen werden.")
    finally:
        db.close()


def _mark_idle() -> None:
    _close_open_outages()
    db = SessionLocal()
    try:
        rows = db.query(CheckStatus).all()
        if not rows:
            return
        for status in rows:
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
