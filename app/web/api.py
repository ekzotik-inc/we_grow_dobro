"""Минимальный HTTP-сервис.

Веб-приложение убрано: весь марафон живёт в чате с ботом. Сервер остаётся только ради
проверки живости — хостинг (Render) считает веб-сервис упавшим, если тот не слушает порт,
и будит уснувший сервис обычным HTTP-запросом.
"""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from .. import services

app = FastAPI(title="We Grow Dobro", docs_url=None, redoc_url=None)


@app.get("/api/health")
async def health() -> dict:
    """Живость сервиса и версия сборки — по ней сразу видно, выкатился ли новый код."""
    cw = services.current_week()
    commit = os.getenv("RENDER_GIT_COMMIT", "")
    return {
        "ok": True,
        "week": cw.number if cw else None,
        "open_weeks": services.open_weeks(),
        "commit": commit[:7],
    }


@app.get("/", response_class=PlainTextResponse)
async def root() -> str:
    return "Марафон добрых дел живёт в Telegram — откройте бота."
