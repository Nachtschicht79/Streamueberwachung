"""Lokaler HLS-Proxy: Playlists umschreiben und Segmente durchreichen."""

from __future__ import annotations

import re
from collections.abc import Iterator
from urllib.parse import quote, urljoin, urlparse

import requests
from fastapi import HTTPException
from fastapi.responses import Response, StreamingResponse

from app.models import Settings

PROXY_PREFIX = "/api/hls/proxy/"
MAX_PLAYLIST_BYTES = 2 * 1024 * 1024
URI_ATTR_RE = re.compile(r'URI=(["\'])(.+?)\1', re.IGNORECASE)
CODECS_ATTR_RE = re.compile(r"\s*,?\s*CODECS=\"[^\"]*\"", re.IGNORECASE)
REQUEST_HEADERS = {
    "User-Agent": "Streamueberwachung/1.0",
    "Accept": "*/*",
}


def _configured_urls(settings: Settings) -> list[str]:
    urls: list[str] = []
    for value in (settings.stream_url, getattr(settings, "backup_stream_url", "")):
        text = (value or "").strip()
        if text:
            urls.append(text)
    return urls


def _origin_key(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    return parsed.scheme.lower(), parsed.netloc.lower()


def _directory_prefix(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if not path.endswith("/"):
        path = path.rsplit("/", 1)[0] + "/"
    return path


def is_allowed_hls_url(url: str, settings: Settings) -> bool:
    """Erlaubt nur http(s)-URLs unter den konfigurierten Stream-Verzeichnissen."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    origin = _origin_key(url)
    path = parsed.path or "/"
    for base in _configured_urls(settings):
        base_parsed = urlparse(base)
        if _origin_key(base) != origin:
            continue
        if path == (base_parsed.path or "/"):
            return True
        prefix = _directory_prefix(base)
        if path.startswith(prefix):
            return True
    return False


def proxy_path(target_url: str) -> str:
    parsed = urlparse(target_url)
    name = (parsed.path.rsplit("/", 1)[-1] or "segment")
    if not name or name in {".", ".."}:
        name = "segment"
    return PROXY_PREFIX + quote(name, safe="._-") + "?url=" + quote(target_url, safe="")


def _looks_like_playlist(url: str, content_type: str, peek: bytes) -> bool:
    path = urlparse(url).path.lower()
    if path.endswith(".m3u8") or path.endswith(".m3u"):
        return True
    ctype = (content_type or "").lower()
    if "mpegurl" in ctype:
        return True
    head = peek.lstrip()[:16]
    return head.startswith(b"#EXTM3U")


def rewrite_playlist(body: str, playlist_url: str) -> str:
    """Macht relative Playlist-URIs absolut und zeigt sie auf den lokalen Proxy."""
    lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append(line)
            continue
        if stripped.startswith("#"):

            def _replace_uri(match: re.Match[str]) -> str:
                quote_char = match.group(1)
                absolute = urljoin(playlist_url, match.group(2))
                return f"URI={quote_char}{proxy_path(absolute)}{quote_char}"

            rewritten_line = URI_ATTR_RE.sub(_replace_uri, line)
            rewritten_line = CODECS_ATTR_RE.sub("", rewritten_line)
            lines.append(rewritten_line)
            continue
        absolute = urljoin(playlist_url, stripped)
        lines.append(proxy_path(absolute))
    rewritten = "\n".join(lines)
    if body.endswith("\n"):
        rewritten += "\n"
    return rewritten


def _read_limited(response: requests.Response, first: bytes) -> bytes:
    chunks = [first]
    total = len(first)
    for part in response.iter_content(64 * 1024):
        if not part:
            continue
        total += len(part)
        if total > MAX_PLAYLIST_BYTES:
            raise HTTPException(status_code=502, detail="Playlist ist zu groß.")
        chunks.append(part)
    return b"".join(chunks)


def _iter_rest(response: requests.Response, first: bytes, session: requests.Session) -> Iterator[bytes]:
    try:
        if first:
            yield first
        for part in response.iter_content(64 * 1024):
            if part:
                yield part
    finally:
        response.close()
        session.close()


def proxy_hls(url: str, settings: Settings) -> Response:
    """Lädt eine erlaubte HLS-URL und gibt Playlist oder Segment zurück."""
    target = (url or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="Keine Stream-URL angegeben.")
    if not is_allowed_hls_url(target, settings):
        raise HTTPException(status_code=403, detail="URL ist nicht als Master- oder Backup-Stream hinterlegt.")

    session = requests.Session()
    session.max_redirects = 5
    try:
        response = session.get(
            target,
            stream=True,
            timeout=(8, 30),
            headers=REQUEST_HEADERS,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        session.close()
        raise HTTPException(status_code=502, detail=f"Stream nicht erreichbar: {exc}") from exc

    try:
        final_url = str(response.url)
        if not is_allowed_hls_url(final_url, settings):
            raise HTTPException(status_code=403, detail="Weiterleitung zeigt auf eine nicht erlaubte URL.")

        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Origin antwortete mit HTTP {response.status_code}.")

        content_type = response.headers.get("Content-Type", "")
        try:
            peek = next(response.iter_content(16 * 1024), b"")
        except requests.RequestException as exc:
            raise HTTPException(status_code=502, detail=f"Stream nicht lesbar: {exc}") from exc

        if _looks_like_playlist(final_url, content_type, peek):
            try:
                raw = _read_limited(response, peek)
            except requests.RequestException as exc:
                raise HTTPException(status_code=502, detail=f"Playlist nicht lesbar: {exc}") from exc
            text = raw.decode("utf-8", errors="replace")
            rewritten = rewrite_playlist(text, final_url)
            response.close()
            response = None
            return Response(
                content=rewritten,
                media_type="application/vnd.apple.mpegurl",
                headers={"Cache-Control": "no-store, max-age=0"},
            )

        media_type = content_type.split(";")[0].strip() if content_type else "video/MP2T"
        stream = _iter_rest(response, peek, session)
        session = None
        response = None
        return StreamingResponse(
            stream,
            media_type=media_type or "video/MP2T",
            headers={"Cache-Control": "no-store, max-age=0"},
        )
    except Exception:
        if response is not None:
            response.close()
        raise
    finally:
        if session is not None:
            session.close()
