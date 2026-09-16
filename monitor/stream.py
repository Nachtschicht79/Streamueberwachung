"""Frame- und Audio-Capture für HLS, HTTP und RTMP."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

FFMPEG_TIMEOUT_SECONDS = 18
AUDIO_SECONDS = 1.5
AUDIO_RATE = 16000


def ffmpeg_available() -> bool:
    """True, wenn ffmpeg im PATH liegt."""
    return shutil.which("ffmpeg") is not None


class StreamCapture:
    """Hält eine OpenCV-Capture offen und fällt bei Fehlern auf ffmpeg zurück."""

    def __init__(self) -> None:
        self._cap: cv2.VideoCapture | None = None
        self._url: str = ""

    def get_frame(self, url: str) -> np.ndarray | None:
        """Liefert den aktuellsten Frame oder None bei Ausfall."""
        url = url.strip()
        if not url:
            return None

        if self._cap is None or self._url != url:
            self._open(url)

        frame = self._read_opencv()
        if frame is not None:
            return frame

        logger.info("OpenCV-Frame fehlgeschlagen, versuche ffmpeg-Fallback.")
        self.release()
        frame = grab_frame_ffmpeg(url)
        if frame is not None:
            self._open(url)
            return frame

        self._open(url)
        return self._read_opencv()

    def release(self) -> None:
        """Schließt die Capture, damit ein Reconnect sauber neu öffnet."""
        if self._cap is not None:
            try:
                self._cap.release()
            except cv2.error:
                pass
            self._cap = None
        self._url = ""

    def _open(self, url: str) -> None:
        self.release()
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 12000)
        if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 12000)
        if not cap.isOpened():
            cap.release()
            logger.warning("OpenCV konnte den Stream nicht öffnen.")
            return
        self._cap = cap
        self._url = url

    def _read_opencv(self) -> np.ndarray | None:
        if self._cap is None or not self._cap.isOpened():
            return None

        # Puffer leeren, damit bei HLS eher ein aktuelles Segment ankommt.
        grabbed = False
        for _ in range(6):
            grabbed = bool(self._cap.grab())
            if not grabbed:
                break

        if not grabbed:
            self.release()
            return None

        ok, frame = self._cap.retrieve()
        if not ok or frame is None or frame.size == 0:
            self.release()
            return None
        return frame


def grab_frame_ffmpeg(url: str) -> np.ndarray | None:
    """Einzelbild über ffmpeg (JPEG über stdout)."""
    if not ffmpeg_available():
        return None

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-analyzeduration",
        "2000000",
        "-probesize",
        "2000000",
        "-i",
        url,
        "-an",
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "mjpeg",
        "pipe:1",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("ffmpeg-Frame-Grab fehlgeschlagen oder Timeout.")
        return None

    if completed.returncode != 0 or not completed.stdout:
        return None

    data = np.frombuffer(completed.stdout, dtype=np.uint8)
    frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if frame is None or frame.size == 0:
        return None
    return frame


def measure_audio_rms(url: str) -> tuple[float | None, str | None]:
    """
    Kurzer PCM-Schnitt über ffmpeg.

    Rückgabe: (RMS 0–1, Hinweis). Hinweis gesetzt, wenn Audio nicht messbar ist
    (kein ffmpeg, kein Audiostream) – dann darf kein Stille-Alarm ausgelöst werden.
    """
    if not ffmpeg_available():
        return None, "ffmpeg fehlt, Audio wird nicht geprüft"

    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-i",
        url,
        "-t",
        str(AUDIO_SECONDS),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(AUDIO_RATE),
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "pipe:1",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, "Audio-Messung fehlgeschlagen"

    if not completed.stdout:
        return None, "Kein Audiostream erkannt"

    samples = np.frombuffer(completed.stdout, dtype=np.int16)
    if samples.size == 0:
        return None, "Kein Audiostream erkannt"

    normalized = samples.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(np.square(normalized))))
    return rms, None


def save_preview(frame: np.ndarray, path: Path) -> bool:
    """Schreibt ein JPEG für Dashboard und Telegram."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        return False
    path.write_bytes(encoded.tobytes())
    return True
