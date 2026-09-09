"""Минимальный HTTP-сервис.

Веб-приложение убрано: весь марафон живёт в чате с ботом. Сервер остаётся только ради
проверки живости — хостинг (Render) считает веб-сервис упавшим, если тот не слушает порт,
и будит уснувший сервис обычным HTTP-запросом.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from ..config import settings

app = FastAPI(title="We Grow Dobro", docs_url=None, redoc_url=None)


@app.get("/api/health")
async def health() -> dict:
    cw = settings.current_week()
    return {"ok": True, "week": cw.number if cw else None, "status": settings.marathon_status()}


@app.get("/", response_class=PlainTextResponse)
async def root() -> str:
    return "Марафон добрых дел живёт в Telegram — откройте бота."
