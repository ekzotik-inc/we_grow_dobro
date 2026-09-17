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
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    MenuButtonCommands,
)

from . import emoji, services
from .bot.handlers import setup_routers
from .bot.middlewares import db_retry, disqualified_guard, premium_emoji_guard
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


PARTICIPANT_COMMANDS = [
    BotCommand(command="start", description="Главное меню"),
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="rules", description="Правила марафона"),
]
STAFF_COMMANDS = PARTICIPANT_COMMANDS + [BotCommand(command="admin", description="Панель P&C")]


async def setup_bot_ui(bot: Bot) -> None:
    """Команда /admin видна только админам и P&C.

    Список команд задаётся отдельно для каждого чата: по умолчанию — без /admin,
    и персонально с ней для тех, кто указан в ADMIN_IDS и PC_IDS. Иначе участники
    видят «Панель P&C» в меню команд Telegram, даже если войти в неё не могут.
    """
    await bot.set_my_commands(PARTICIPANT_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    for staff_id in sorted(settings.admin_ids | settings.pc_ids):
        try:
            await bot.set_my_commands(STAFF_COMMANDS, scope=BotCommandScopeChat(chat_id=staff_id))
        except Exception as ex:  # noqa: BLE001
            log.warning("не удалось выдать /admin пользователю %s: %s", staff_id, ex)
    # Кнопка меню возвращается к обычным командам: мини-приложения у бота больше нет.
    await bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def verify_emoji(bot: Bot) -> None:
    """Проверить, какие премиум-эмодзи Telegram вообще отдаёт этому боту.

    Набор присылает заказчик, и в нём попадаются идентификаторы, недоступные боту.
    Один такой символ ломает каждое сообщение, где он встретился, — поэтому спрашиваем
    Telegram заранее и всё, чего он не знает, показываем обычным.
    """
    if not emoji.enabled():
        return
    ids = emoji.used_ids()
    known: set[str] = set()
    for start in range(0, len(ids), 200):  # ограничение метода — 200 идентификаторов за раз
        batch = ids[start : start + 200]
        try:
            stickers = await bot.get_custom_emoji_stickers(custom_emoji_ids=batch)
        except Exception as ex:  # noqa: BLE001
            log.warning("не удалось проверить премиум-эмодзи: %s", ex)
            return
        known.update(x.custom_emoji_id for x in stickers if x.custom_emoji_id)
    missing = [x for x in ids if x not in known]
    for emoji_id in missing:
        emoji.drop(emoji_id)
    if missing:
        log.warning("недоступных премиум-эмодзи: %s — показываю их обычными", len(missing))
    else:
        log.info("премиум-эмодзи проверены: доступны все %s", len(ids))


async def run() -> None:
    if not settings.bot_token:
        log.error("BOT_TOKEN не задан. Впишите токен от @BotFather в файл .env (см. .env.example).")
        sys.exit(EXIT_CONFIG_ERROR)
    await init_db()
    async with SessionLocal() as s:
        open_weeks = await services.load_open_weeks(s)
        demoted = await services.sync_staff_flags(s)
        # Исправленные владельцем картинки премиум-эмодзи: набор присылает заказчик,
        # и в нём попадаются символы, нарисованные не тем.
        fixed = emoji.apply_overrides(await services.get_setting(s, "emoji_overrides"))
        await s.commit()
    if fixed:
        log.info("заменено премиум-эмодзи: %s", fixed)
    if demoted:
        log.info("права приведены к спискам ADMIN_IDS/PC_IDS: изменено пользователей — %s", demoted)
    log.info("открытые недели: %s", open_weeks or "ни одной — участники видят «задания скоро»")

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await verify_emoji(bot)
    bot.session.middleware(premium_emoji_guard)
    dp = Dispatcher(storage=MemoryStorage())
    # База на бесплатном тарифе засыпает: первое действие после паузы повторяем, а не теряем.
    dp.update.outer_middleware(db_retry)
    # Проверка «снят с марафона» — сразу после повторов к базе и до всех обработчиков.
    dp.update.outer_middleware(disqualified_guard)
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
