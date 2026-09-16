"""Startet den Monitor-Worker und den lokalen Webserver zusammen."""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn

from app.database import init_db
from monitor.worker import run_worker

HOST = "127.0.0.1"
PORT = 8000


def main() -> None:
    init_db()
    try:
        multiprocessing.set_start_method("spawn", force=True)
    except RuntimeError:
        pass

    worker = multiprocessing.Process(
        target=run_worker,
        name="stream-monitor",
        daemon=True,
    )
    worker.start()
    print(f"Streamüberwachung läuft unter http://{HOST}:{PORT}")
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False, log_level="info")


if __name__ == "__main__":
    main()
