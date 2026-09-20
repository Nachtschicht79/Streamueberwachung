"""Gemeinsame Bezeichner für Webstream- und Makalu-Überwachung."""

from __future__ import annotations

SOURCE_WEBSTREAM = "webstream"
SOURCE_MAKALU = "makalu"
MONITOR_BOTH = "both"

MONITOR_TARGETS = (SOURCE_WEBSTREAM, SOURCE_MAKALU, MONITOR_BOTH)

SOURCE_LABELS: dict[str, str] = {
    SOURCE_WEBSTREAM: "Webstream",
    SOURCE_MAKALU: "Makalu",
}

MONITOR_LABELS: dict[str, str] = {
    SOURCE_WEBSTREAM: "Nur Webstream",
    SOURCE_MAKALU: "Nur Makalu",
    MONITOR_BOTH: "Beide Streams",
}


def normalize_monitor_target(value: str | None) -> str:
    """Unbekannte Werte fallen auf den Webstream zurück."""
    target = (value or SOURCE_WEBSTREAM).strip().lower()
    return target if target in MONITOR_TARGETS else SOURCE_WEBSTREAM


def normalize_source(value: str | None) -> str:
    source = (value or SOURCE_WEBSTREAM).strip().lower()
    return source if source in SOURCE_LABELS else SOURCE_WEBSTREAM


def source_label(source: str | None) -> str:
    return SOURCE_LABELS[normalize_source(source)]


def selected_sources(target: str | None) -> list[str]:
    """Welche Quellen zur gewählten Überwachungsoption gehören."""
    normalized = normalize_monitor_target(target)
    if normalized == SOURCE_MAKALU:
        return [SOURCE_MAKALU]
    if normalized == MONITOR_BOTH:
        return [SOURCE_WEBSTREAM, SOURCE_MAKALU]
    return [SOURCE_WEBSTREAM]
