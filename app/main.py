"""Entrypoint: runs the Telegram bot (long polling) and the Web App server in one process."""
from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from .bot.handlers import setup_routers
from .config import settings
from .db import init_db
from .scheduler import build_scheduler
from .web.api import app as web_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")


async def setup_bot_ui(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="menu", description="Главное меню"),
            BotCommand(command="rules", description="Правила марафона"),
            BotCommand(command="admin", description="Панель P&C (только для P&C)"),
        ]
    )
    if settings.webapp_url.startswith("https://"):
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="Марафон", web_app=WebAppInfo(url=settings.webapp_url + "/")))


async def run() -> None:
    if not settings.bot_token:
        log.error("BOT_TOKEN is not set (see .env.example)")
        sys.exit(1)
    await init_db()

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(setup_routers())
    await setup_bot_ui(bot)

    scheduler = build_scheduler(bot)
    scheduler.start()

    config = uvicorn.Config(web_app, host=settings.web_host, port=settings.web_port, log_level="info")
    server = uvicorn.Server(config)

    me = await bot.get_me()
    log.info("bot @%s started; web app on %s:%s; weeks: %s", me.username, settings.web_host, settings.web_port, [(w.number, str(w.start), str(w.end)) for w in settings.weeks])
    await bot.delete_webhook(drop_pending_updates=False)
    try:
        await asyncio.gather(dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()), server.serve())
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())
