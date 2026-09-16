"""FastAPI-App: Weboberfläche und REST-API auf localhost:8000."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import BASE_DIR, init_db
from app.routes import router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Streamüberwachung",
    description="Lokale Überwachung eines Livestreams mit Telegram-Alarmen.",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.include_router(router)
