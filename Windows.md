# Streamüberwachung unter Windows

Diese Anleitung beschreibt, welche Dateien du auf den Windows-Rechner kopierst, welche Programme installiert sein müssen und wie die App gestartet wird. Die Oberfläche läuft danach lokal unter [http://localhost:8000](http://localhost:8000).

## 1. Welche Dateien werden benötigt?

Nur den **Quellcode** kopieren. Das macOS-Verzeichnis `.venv` und den Ordner `data/` nicht mitnehmen – beide werden unter Windows neu erzeugt.

### Pflicht (App startet ohne diese Dateien nicht)

```
Streamueberwachung/
├── main.py
├── requirements.txt
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── database.py
│   ├── models.py
│   ├── routes.py
│   └── schemas.py
├── monitor/
│   ├── __init__.py
│   ├── worker.py
│   ├── stream.py
│   ├── detector.py
│   └── telegram.py
├── static/
│   └── css/
│       └── style.css
└── templates/
    └── index.html
```

Die Ordnerstruktur muss so bleiben. `main.py` gehört in denselben Ordner wie `app/`, `monitor/`, `static/` und `templates/`.

### Optional

| Datei | Zweck |
| --- | --- |
| `README.md` | Allgemeine Bedienung |
| `Windows.md` | Diese Anleitung |
| `.gitignore` | Nur relevant, wenn du mit Git arbeitest |

### Nicht kopieren

| Pfad | Grund |
| --- | --- |
| `.venv/` | Enthält macOS-Python. Unter Windows immer neu anlegen. |
| `data/` | Wird beim ersten Start automatisch erzeugt. |
| `__pycache__/` | Temporäre Python-Dateien, plattformabhängig. |
| `.DS_Store`, `._*` | macOS-Systemdateien |

### Was die App selbst anlegt

Beim ersten Start entsteht der Ordner `data/`:

- `data/streamwatch.db` – Einstellungen, Status und Alarmhistorie (SQLite)
- `data/preview.jpg` – aktuelles Vorschaubild (erst nach einem erfolgreichen Stream-Check)

Telegram-Token und Chat-ID liegen in der Datenbank, nicht in einer `.env`-Datei.

---

## 2. Voraussetzungen

- **Windows 10 oder 11**
- **Python 3.11 oder neuer** (64-Bit)
- **ffmpeg** im System-PATH (Audio-Prüfung und Fallback, wenn OpenCV den Stream nicht öffnet)

Ohne ffmpeg startet die App trotzdem. Bild (Schwarzbild, Freeze, Offline) wird weiter geprüft. Stille wird dann **nicht** als Alarm gewertet.

### Python installieren

Empfohlen über den offiziellen Installer: [https://www.python.org/downloads/windows/](https://www.python.org/downloads/windows/)

Beim Installer **unbedingt** aktivieren:

- **Add python.exe to PATH**
- **Install launcher for all users** (optional, aber praktisch)

Danach ein **neues** Terminal öffnen und prüfen:

```bat
python --version
```

Erwartet wird etwas wie `Python 3.12.x`. Wenn `python` nicht gefunden wird, den Python Launcher nutzen:

```bat
py --version
py -3 --version
```

Falls Windows den Microsoft-Store-Stub öffnet statt Python: in *Einstellungen → Apps → Erweiterte App-Einstellungen → App-Ausführungsaliase* die Aliase für `python.exe` und `python3.exe` deaktivieren, danach Python neu installieren.

### ffmpeg installieren

**Variante A – winget** (Windows 11 / aktuelles Windows 10):

```bat
winget install Gyan.FFmpeg
```

**Variante B – manueller Download:**

1. Build von [https://www.gyan.dev/ffmpeg/builds/](https://www.gyan.dev/ffmpeg/builds/) laden (`ffmpeg-release-essentials.zip`).
2. Entpacken, z. B. nach `C:\ffmpeg`.
3. Den Ordner `C:\ffmpeg\bin` in den PATH aufnehmen:
   - *Einstellungen → System → Info → Erweiterte Systemeinstellungen → Umgebungsvariablen*
   - Bei **Benutzervariablen** oder **Systemvariablen** den Eintrag `Path` bearbeiten
   - Neu: `C:\ffmpeg\bin`

Danach ein **neues** Terminal öffnen und prüfen:

```bat
ffmpeg -version
```

Wenn der Befehl unbekannt ist, ist ffmpeg nicht im PATH. Die App findet ffmpeg nur über den PATH (`ffmpeg` als Befehl), nicht über einen beliebigen Installationsordner.

---

## 3. Installation (einmalig)

Eingabeaufforderung oder PowerShell im **Projektordner** öffnen (dort, wo `main.py` liegt).

### Eingabeaufforderung (cmd)

```bat
cd /d C:\Pfad\zu\Streamueberwachung

python -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### PowerShell

```powershell
cd C:\Pfad\zu\Streamueberwachung

python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Wenn PowerShell die Aktivierung mit *„Die Ausführung von Skripts ist auf diesem System deaktiviert“* blockiert:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Danach `Activate.ps1` erneut ausführen. Alternativ ohne Policy-Änderung die cmd-Variante nutzen oder Python direkt aufrufen:

```bat
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Die Installation von `opencv-python` und `numpy` kann einige Minuten dauern.

---

## 4. Start

Im Projektordner, mit aktivierter Umgebung:

```bat
python main.py
```

Ohne Aktivierung:

```bat
.venv\Scripts\python.exe main.py
```

In der Konsole erscheint:

```text
Streamüberwachung läuft unter http://127.0.0.1:8000
```

Browser öffnen: [http://localhost:8000](http://localhost:8000)

Das Fenster **offen lassen**. Schließen beendet Webserver und Überwachung.

Beenden: `Strg+C` im Terminal.

---

## 5. Jeder weitere Start

```bat
cd /d C:\Pfad\zu\Streamueberwachung
.venv\Scripts\activate.bat
python main.py
```

PowerShell:

```powershell
cd C:\Pfad\zu\Streamueberwachung
.\.venv\Scripts\Activate.ps1
python main.py
```

`pip install` ist nur nötig, wenn `requirements.txt` sich geändert hat oder `.venv` neu angelegt wurde.

---

## 6. Bedienung

1. Stream-URL eintragen (typisch eine `.m3u8`-Adresse, auch HTTP oder RTMP).
2. Telegram Bot-Token und Chat-ID eintragen.
3. Schwellenwerte bei Bedarf anpassen und **Speichern**.
4. **Telegram testen** sendet eine Nachricht und zeigt Erfolg oder Fehler an.
5. **Überwachung starten** / **stoppen**.

Ein Alarm wird erst nach zwei aufeinanderfolgenden Fehlprüfungen desselben Typs gesendet. Danach gilt der eingestellte Cooldown.

Die App bindet nur `127.0.0.1` (localhost). Sie ist nicht aus dem Netzwerk erreichbar.

---

## 7. Typische Windows-Probleme

| Symptom | Ursache / Lösung |
| --- | --- |
| `python` nicht gefunden | Python neu installieren, Haken **Add python.exe to PATH**, neues Terminal. Oder `py -3 main.py` verwenden. |
| `python` öffnet den Microsoft Store | App-Ausführungsaliase für Python deaktivieren. |
| `Activate.ps1` wird blockiert | Execution Policy setzen oder `activate.bat` in cmd nutzen. |
| `ffmpeg` nicht gefunden | PATH prüfen, **neues** Terminal, `ffmpeg -version`. |
| Port 8000 belegt | Anderen Prozess beenden oder in `main.py` `PORT` ändern. |
| OpenCV / numpy-Installationsfehler | 64-Bit-Python 3.11+ verwenden, nicht 32-Bit. |
| Stream wird nicht erkannt, Audio fehlt | ffmpeg fehlt oder ist nicht im PATH. Bildprüfung kann trotzdem laufen. |
| Firewall-Hinweis | Für `127.0.0.1` normalerweise nicht nötig. Falls doch: private Netzwerke erlauben. |
| `.venv` vom Mac kopiert | Löschen und unter Windows neu anlegen (`python -m venv .venv`). |
| Antivirus blockiert ffmpeg oder Python | Ausnahme für Projektordner und `ffmpeg.exe` setzen. |

Nach Änderungen an PATH oder Python immer ein neues Terminal öffnen. Bereits offene Fenster kennen die alten Variablen.

---

## 8. Kurzcheck vor dem ersten Start

```bat
python --version
ffmpeg -version
cd /d C:\Pfad\zu\Streamueberwachung
dir main.py
dir requirements.txt
dir app
dir monitor
dir static
dir templates
```

Wenn Python-Version, ffmpeg und die genannten Dateien stimmen:

```bat
python -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
python main.py
```
