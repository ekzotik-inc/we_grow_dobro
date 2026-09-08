"""Outer session middleware: keeps premium emoji from ever breaking a message.

Telegram accepts custom emoji only while the bot owner holds Telegram Premium. If that lapses,
every send would start failing. Instead of letting that happen, the first rejection strips the
custom emoji from the request, retries it once, and switches the whole bot to plain emoji.
"""
from __future__ import annotations

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


async def premium_emoji_guard(make_request, bot, method):
    try:
        return await make_request(bot, method)
    except TelegramBadRequest as ex:
        message = str(ex)
        if not (_has_custom_emoji(method) and any(m.lower() in message.lower() for m in _EMOJI_ERRORS)):
            raise
        emoji.disable(f"Telegram отклонил премиум-эмодзи: {message}")
        _strip_custom_emoji(method)
        return await make_request(bot, method)
