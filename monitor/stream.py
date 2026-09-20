"""Frame- und Audio-Capture für HLS, HTTP und RTMP."""

from __future__ import annotations

import logging
import math
import shutil
import subprocess
import threading
import time
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

FFMPEG_TIMEOUT_SECONDS = 18
AUDIO_SECONDS = 1.5
AUDIO_RATE = 16000
DBFS_FLOOR = -100.0
# Frames älter als das gelten als unbrauchbar (HLS-Segmente können einige Sekunden klaffen).
READER_FRAME_MAX_AGE_SECONDS = 8.0
READER_FRAME_WAIT_SECONDS = 12.0


def rms_to_dbfs(rms: float) -> float:
    """Wandelt linearen Full-Scale-RMS (0–1) in RMS-dBFS um."""
    if rms <= 0:
        return DBFS_FLOOR
    return max(DBFS_FLOOR, 20.0 * math.log10(max(rms, 1e-10)))


def ffmpeg_available() -> bool:
    """True, wenn ffmpeg im PATH liegt."""
    return shutil.which("ffmpeg") is not None


class StreamCapture:
    """Hält eine OpenCV-Capture offen, liest Frames durchgehend und fällt auf ffmpeg zurück."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cap: cv2.VideoCapture | None = None
        self._url: str = ""
        self._latest: np.ndarray | None = None
        self._latest_at: float = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._generation: int = 0

    def get_frame(self, url: str) -> np.ndarray | None:
        """Liefert den aktuellsten Frame oder None bei Ausfall."""
        url = url.strip()
        if not url:
            return None

        if self._url != url or not self._reader_alive():
            self._open(url)

        frame = self._wait_for_fresh_frame(READER_FRAME_WAIT_SECONDS)
        if frame is not None:
            return frame

        logger.info("OpenCV-Frame fehlgeschlagen, versuche ffmpeg-Fallback.")
        self.release()
        frame = grab_frame_ffmpeg(url)
        if frame is not None:
            self._open(url)
            return frame

        self._open(url)
        return self._wait_for_fresh_frame(READER_FRAME_WAIT_SECONDS)

    def release(self) -> None:
        """Stoppt den Lesethread und schließt die Capture."""
        thread = self._thread
        self._thread = None
        with self._lock:
            self._generation += 1
            self._stop.set()
            cap = self._cap
            self._cap = None
            self._latest = None
            self._latest_at = 0.0
            self._url = ""
        if cap is not None:
            try:
                cap.release()
            except cv2.error:
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    def _open(self, url: str) -> None:
        self.release()
        self._stop.clear()
        with self._lock:
            generation = self._generation
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
        if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 12000)
        if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 12000)
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not cap.isOpened():
            cap.release()
            logger.warning("OpenCV konnte den Stream nicht öffnen.")
            return
        with self._lock:
            self._cap = cap
            self._url = url
            self._latest = None
            self._latest_at = 0.0
        self._thread = threading.Thread(
            target=self._reader_loop,
            args=(generation,),
            name="stream-reader",
            daemon=True,
        )
        self._thread.start()

    def _reader_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _reader_loop(self, generation: int) -> None:
        """Liest in Echtzeit, damit der ffmpeg-Puffer nicht volläuft."""
        failures = 0
        while not self._stop.is_set() and generation == self._generation:
            with self._lock:
                if generation != self._generation:
                    break
                cap = self._cap
            if cap is None:
                break
            try:
                ok, frame = cap.read()
            except cv2.error:
                ok, frame = False, None
            if generation != self._generation or self._stop.is_set():
                break
            if ok and frame is not None and frame.size > 0:
                copied = frame.copy()
                with self._lock:
                    if generation != self._generation:
                        break
                    self._latest = copied
                    self._latest_at = time.monotonic()
                failures = 0
                continue
            failures += 1
            time.sleep(0.05 if failures < 8 else 0.25)

    def _snapshot_fresh(self) -> np.ndarray | None:
        with self._lock:
            frame = self._latest
            grabbed_at = self._latest_at
            if frame is None or frame.size == 0:
                return None
            age = time.monotonic() - grabbed_at
            if age > READER_FRAME_MAX_AGE_SECONDS:
                return None
            return frame.copy()

    def _wait_for_fresh_frame(self, timeout: float) -> np.ndarray | None:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            frame = self._snapshot_fresh()
            if frame is not None:
                return frame
            if time.monotonic() >= deadline:
                return None
            if not self._reader_alive():
                return None
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(0.05, remaining))


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

    Rückgabe: (RMS-dBFS, Hinweis). Hinweis gesetzt, wenn Audio nicht messbar ist
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
    return rms_to_dbfs(rms), None


def save_preview(frame: np.ndarray, path: Path) -> bool:
    """Schreibt ein JPEG für Dashboard und Telegram."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        return False
    path.write_bytes(encoded.tobytes())
    return True
