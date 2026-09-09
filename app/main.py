"""Entrypoint: runs the Telegram bot (long polling) and the Web App server in one process."""
from __future__ import annotations

import asyncio
import logging
import sys

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, MenuButtonCommands

from . import services
from .bot.handlers import setup_routers
from .bot.middlewares import premium_emoji_guard
from .config import settings
from .db import SessionLocal, init_db
from .scheduler import build_scheduler
from .web.api import app as web_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("main")

# Exit code for a configuration error the process cannot recover from by retrying (sysexits.h EX_CONFIG).
# The systemd unit lists it in RestartPreventExitStatus, so a wrong token stops the service with a clear
# message instead of crash-looping every 10 seconds and flooding the journal.
EXIT_CONFIG_ERROR = 78


async def setup_bot_ui(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="menu", description="Главное меню"),
            BotCommand(command="rules", description="Правила марафона"),
            BotCommand(command="admin", description="Панель P&C (только для P&C)"),
        ]
    )
    # Кнопка меню возвращается к обычным командам: мини-приложения у бота больше нет.
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def run() -> None:
    if not settings.bot_token:
        log.error("BOT_TOKEN не задан. Впишите токен от @BotFather в файл .env (см. .env.example).")
        sys.exit(EXIT_CONFIG_ERROR)
    await init_db()
    async with SessionLocal() as s:
        open_weeks = await services.load_open_weeks(s)
    log.info("открытые недели: %s", open_weeks or "ни одной — участники видят «задания скоро»")

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session.middleware(premium_emoji_guard)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(setup_routers())
    scheduler = None
    try:
        me = await bot.get_me()
        await setup_bot_ui(bot)

        scheduler = build_scheduler(bot)
        scheduler.start()

        config = uvicorn.Config(web_app, host=settings.web_host, port=settings.web_port, log_level="info")
        server = uvicorn.Server(config)

        log.info(
            "bot @%s started; web app on %s:%s; weeks: %s",
            me.username, settings.web_host, settings.web_port, [(w.number, str(w.start), str(w.end)) for w in settings.weeks],
        )
        await bot.delete_webhook(drop_pending_updates=False)
        # resolve_used_update_types() misses channel_post, which we need for cards posted in the two channels.
        allowed = sorted(set(dp.resolve_used_update_types()) | {"channel_post", "edited_channel_post", "callback_query", "my_chat_member"})
        await asyncio.gather(dp.start_polling(bot, allowed_updates=allowed), server.serve())
    except TelegramUnauthorizedError:
        # Retrying will never help: the token itself is wrong.
        log.error(
            "Telegram отклонил BOT_TOKEN. Проверьте, что токен в .env скопирован целиком и без пробелов "
            "(взять заново: @BotFather → /mybots → ваш бот → API Token), затем перезапустите бота."
        )
        sys.exit(EXIT_CONFIG_ERROR)
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run())
