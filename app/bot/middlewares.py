"""Outer session middleware: keeps premium emoji from ever breaking a message.

Telegram accepts custom emoji only while the bot owner holds Telegram Premium. If that lapses,
every send would start failing. Instead of letting that happen, the first rejection strips the
custom emoji from the request, retries it once, and switches the whole bot to plain emoji.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram.exceptions import TelegramBadRequest

from .. import emoji

log = logging.getLogger(__name__)

# Substrings Telegram uses when it refuses a custom emoji entity.
_EMOJI_ERRORS = ("custom emoji", "custom_emoji", "emoji is not allowed", "EMOJI_INVALID")

_TEXT_FIELDS = ("text", "caption")


def _buttons(method):
    markup = getattr(method, "reply_markup", None)
    for row in getattr(markup, "inline_keyboard", None) or []:
        yield from row


def _has_custom_emoji(method) -> bool:
    if any("<tg-emoji" in (getattr(method, f, None) or "") for f in _TEXT_FIELDS):
        return True
    return any(getattr(b, "icon_custom_emoji_id", None) for b in _buttons(method))


def _strip_custom_emoji(method) -> None:
    for field in _TEXT_FIELDS:
        value = getattr(method, field, None)
        if value and "<tg-emoji" in value:
            object.__setattr__(method, field, emoji.strip(value))
    # Button icons carry no fallback of their own: put the plain character back in front of the label.
    for button in _buttons(method):
        icon = getattr(button, "icon_custom_emoji_id", None)
        if icon:
            object.__setattr__(button, "text", f"{emoji.char_for_id(icon)} {button.text}".strip())
            object.__setattr__(button, "icon_custom_emoji_id", None)


def _premiumize(method) -> None:
    """Wrap plain emoji of an outgoing message into premium ones.

    Only for private chats and groups: a channel post with a custom emoji is rejected by Telegram,
    and channel cards are rendered plain on purpose (app/bot/channels.py). Channel ids are negative,
    user ids are positive, so the destination decides.
    """
    chat_id = getattr(method, "chat_id", None)
    if not isinstance(chat_id, int) or chat_id < 0:
        return
    for field in _TEXT_FIELDS:
        value = getattr(method, field, None)
        if value:
            object.__setattr__(method, field, emoji.rich(value))
    for button in _buttons(method):
        if getattr(button, "icon_custom_emoji_id", None):
            continue
        icon, label = emoji.button_icon(getattr(button, "text", "") or "")
        if icon:
            object.__setattr__(button, "text", label)
            object.__setattr__(button, "icon_custom_emoji_id", icon)


async def premium_emoji_guard(make_request, bot, method):
    _premiumize(method)
    try:
        return await make_request(bot, method)
    except TelegramBadRequest as ex:
        message = str(ex)
        if not (_has_custom_emoji(method) and any(m.lower() in message.lower() for m in _EMOJI_ERRORS)):
            raise
        emoji.disable(f"Telegram отклонил премиум-эмодзи: {message}")
        _strip_custom_emoji(method)
        return await make_request(bot, method)


# ---------- устойчивость к засыпающей базе ----------

_DB_ERRORS = ("InterfaceError", "OperationalError", "ConnectionDoesNotExistError",
              "CannotConnectNowError", "TimeoutError", "ConnectionResetError")


def _is_db_hiccup(ex: Exception) -> bool:
    """Похоже ли это на «база не ответила» — то, что лечится повтором."""
    names = {type(ex).__name__}
    cause = ex.__cause__
    while cause is not None:
        names.add(type(cause).__name__)
        cause = cause.__cause__
    return bool(names & set(_DB_ERRORS))


def _chat_id(event) -> int | None:
    message = getattr(event, "message", None) or getattr(event, "edited_message", None)
    if message is not None:
        return message.chat.id
    cq = getattr(event, "callback_query", None)
    if cq is not None and cq.message is not None:
        return cq.message.chat.id
    return None


# Пробуждение уснувшей базы занимает до десяти секунд, поэтому пауз три и они растут.
_RETRY_DELAYS = (1, 3, 6)


async def db_retry(handler, event, data):
    """Повторить действие, если база спала и не ответила с первого раза.

    Без этого первое сообщение после паузы терялось молча: участник отправлял номер,
    запрос падал, а он видел тишину и думал, что бот сломан. На платном тарифе база
    не засыпает и повторы просто не понадобятся.
    """
    last: Exception | None = None
    for attempt, delay in enumerate((0, *_RETRY_DELAYS)):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await handler(event, data)
        except Exception as ex:  # noqa: BLE001
            if not _is_db_hiccup(ex):
                raise
            last = ex
            log.warning("База не ответила (%s), попытка %s из %s",
                        type(ex).__name__, attempt + 1, len(_RETRY_DELAYS) + 1)
    log.error("База так и не ответила: %s", last)
    chat_id = _chat_id(event)
    bot = data.get("bot")
    if chat_id and bot:
        with contextlib.suppress(Exception):
            await bot.send_message(
                chat_id,
                "⏳ База данных просыпается — не успел обработать. "
                "Повтори последнее действие, пожалуйста.",
            )
    return None
