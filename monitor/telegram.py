"""Telegram Bot API: Textnachrichten und optionale Screenshots."""

from __future__ import annotations

from pathlib import Path

import requests

TELEGRAM_TIMEOUT_SECONDS = 20


def send_message(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    """Sendet eine Textnachricht. Gibt (ok, Hinweis) zurück, ohne den Token zu loggen."""
    if not token.strip() or not chat_id.strip():
        return False, "Bot-Token oder Chat-ID fehlt."

    url = f"https://api.telegram.org/bot{token.strip()}/sendMessage"
    try:
        response = requests.post(
            url,
            json={"chat_id": chat_id.strip(), "text": text},
            timeout=TELEGRAM_TIMEOUT_SECONDS,
        )
        payload = _safe_json(response)
        if payload.get("ok"):
            return True, "Nachricht gesendet."
        return False, _telegram_error(payload, response.status_code)
    except requests.RequestException as exc:
        return False, f"Netzwerkfehler: {exc}"


def send_photo(token: str, chat_id: str, image_path: Path, caption: str) -> tuple[bool, str]:
    """Sendet ein JPEG als Foto. Fällt auf reine Textnachricht zurück, wenn die Datei fehlt."""
    if not image_path.is_file():
        return send_message(token, chat_id, caption)

    url = f"https://api.telegram.org/bot{token.strip()}/sendPhoto"
    try:
        with image_path.open("rb") as handle:
            response = requests.post(
                url,
                data={"chat_id": chat_id.strip(), "caption": caption},
                files={"photo": ("preview.jpg", handle, "image/jpeg")},
                timeout=TELEGRAM_TIMEOUT_SECONDS,
            )
        payload = _safe_json(response)
        if payload.get("ok"):
            return True, "Nachricht mit Screenshot gesendet."
        return False, _telegram_error(payload, response.status_code)
    except requests.RequestException as exc:
        return False, f"Netzwerkfehler: {exc}"


def send_test(token: str, chat_id: str) -> tuple[bool, str]:
    """Kurzer Verbindungstest für die Weboberfläche."""
    return send_message(
        token,
        chat_id,
        "Streamüberwachung: Telegram-Test erfolgreich. Alarme kommen in diesem Chat an.",
    )


def _safe_json(response: requests.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _telegram_error(payload: dict, status_code: int) -> str:
    description = payload.get("description")
    if isinstance(description, str) and description:
        return description
    return f"Telegram-Fehler (HTTP {status_code})."
