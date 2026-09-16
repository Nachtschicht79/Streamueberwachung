"""Bild- und Audio-Auswertung: Schwarzbild, Freeze und Stille."""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

ALERT_BLACK = "black"
ALERT_FREEZE = "freeze"
ALERT_SILENCE = "silence"
ALERT_OFFLINE = "offline"

ALERT_LABELS: dict[str, str] = {
    ALERT_BLACK: "Schwarzbild",
    ALERT_FREEZE: "Eingefrorenes Bild",
    ALERT_SILENCE: "Kein Ton",
    ALERT_OFFLINE: "Stream-Ausfall",
}

# Kleinere Graustufen-Kopie für Freeze-Vergleich (stabiler und schneller).
_COMPARE_WIDTH = 320


@dataclass
class CheckMetrics:
    """Messwerte eines Prüfdurchlaufs."""

    brightness: float | None = None
    frame_diff: float | None = None
    audio_rms: float | None = None
    issues: list[str] = field(default_factory=list)
    message: str = ""
    gray: np.ndarray | None = None


def to_compare_gray(frame: np.ndarray) -> np.ndarray:
    """Skaliert einen BGR-Frame auf eine schmale Graustufen-Version."""
    height, width = frame.shape[:2]
    if width <= 0 or height <= 0:
        raise ValueError("Leerer Frame")
    new_height = max(1, int(height * (_COMPARE_WIDTH / width)))
    small = cv2.resize(frame, (_COMPARE_WIDTH, new_height), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)


def brightness_of(gray: np.ndarray) -> float:
    """Mittlere Helligkeit (0–255)."""
    return float(np.mean(gray))


def mean_abs_diff(previous: np.ndarray, current: np.ndarray) -> float:
    """Mittlere absolute Pixel-Differenz zweier Graustufenbilder."""
    if previous.shape != current.shape:
        current = cv2.resize(current, (previous.shape[1], previous.shape[0]), interpolation=cv2.INTER_AREA)
    delta = np.abs(previous.astype(np.float32) - current.astype(np.float32))
    return float(np.mean(delta))


def analyze_video(
    frame: np.ndarray,
    previous_gray: np.ndarray | None,
    brightness_threshold: float,
    freeze_threshold: float,
) -> CheckMetrics:
    """Prüft Schwarzbild und Freeze anhand eines Frames."""
    metrics = CheckMetrics()
    gray = to_compare_gray(frame)
    metrics.gray = gray
    metrics.brightness = brightness_of(gray)

    if metrics.brightness < brightness_threshold:
        metrics.issues.append(ALERT_BLACK)

    if previous_gray is not None:
        metrics.frame_diff = mean_abs_diff(previous_gray, gray)
        if metrics.frame_diff < freeze_threshold:
            metrics.issues.append(ALERT_FREEZE)

    return metrics


def apply_silence(metrics: CheckMetrics, audio_rms: float | None, audio_threshold: float) -> None:
    """Ergänzt Stille, falls ein gültiger Audio-RMS vorliegt."""
    metrics.audio_rms = audio_rms
    if audio_rms is not None and audio_rms < audio_threshold:
        metrics.issues.append(ALERT_SILENCE)


def summarize(metrics: CheckMetrics, audio_note: str | None = None) -> str:
    """Lesbare Statuszeile für Dashboard und Telegram."""
    if ALERT_OFFLINE in metrics.issues:
        return "Stream nicht erreichbar oder kein Bild empfangen."

    parts: list[str] = []
    if metrics.issues:
        parts.append(", ".join(ALERT_LABELS[item] for item in metrics.issues if item in ALERT_LABELS))
    else:
        parts.append("Stream wirkt in Ordnung.")

    details: list[str] = []
    if metrics.brightness is not None:
        details.append(f"Helligkeit {metrics.brightness:.1f}")
    if metrics.frame_diff is not None:
        details.append(f"Frame-Diff {metrics.frame_diff:.2f}")
    if metrics.audio_rms is not None:
        details.append(f"Audio-RMS {metrics.audio_rms:.4f}")
    elif audio_note:
        details.append(audio_note)

    if details:
        parts.append(" · ".join(details))
    return " – ".join(parts)
