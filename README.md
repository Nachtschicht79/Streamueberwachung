# Streamüberwachung

Lokales Tool zur Überwachung eines Livestreams (HLS `.m3u8`, HTTP oder RTMP). Bei Schwarzbild, eingefrorenem Bild, Stille oder Stream-Ausfall wird eine Telegram-Nachricht gesendet. Steuerung und Konfiguration laufen über eine Weboberfläche unter [http://localhost:8000](http://localhost:8000).

## Voraussetzungen

- Python 3.11 oder neuer
- [ffmpeg](https://ffmpeg.org/) im System-PATH (Audio-Prüfung und Fallback, wenn OpenCV den Stream nicht öffnet)

macOS mit Homebrew:

```bash
brew install ffmpeg
```

## Installation

Im Projektordner:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Start

```bash
python main.py
```

Damit starten Webserver und Monitor-Worker gemeinsam. Die Oberfläche ist unter `http://localhost:8000` erreichbar.

## Bedienung

1. Stream-URL eintragen (typisch eine `.m3u8`-Adresse).
2. Telegram Bot-Token und Chat-ID eintragen.
3. Schwellenwerte bei Bedarf anpassen und **Speichern**.
4. **Telegram testen** sendet eine Nachricht und zeigt Erfolg oder Fehler an.
5. **Überwachung starten** / **stoppen**.

Ein Alarm wird erst nach zwei aufeinanderfolgenden Fehlprüfungen desselben Typs gesendet. Danach gilt der eingestellte Cooldown.

## Standard-Schwellen

| Einstellung | Default | Bedeutung |
| --- | --- | --- |
| Helligkeit | 18 | Mittelwert 0–255, darunter Schwarzbild |
| Frame-Differenz | 2.5 | Kaum Bewegung zwischen Frames = Freeze |
| Audio-RMS | 0.008 | Sehr leiser Pegel = Stille |
| Prüfintervall | 10 s | Abstand zwischen den Checks |
| Cooldown | 120 s | Mindestabstand gleicher Alarme |

Ohne ffmpeg wird das Bild weiter geprüft, Stille aber nicht als Alarm gewertet.

Einstellungen, Status und Alarmhistorie liegen lokal in `data/streamwatch.db`.
