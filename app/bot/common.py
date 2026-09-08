"""Helpers shared by handlers: in-place message editing, DB session, user loading."""
from __future__ import annotations

import contextlib
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    Message,
)

from pathlib import Path

from ..db import SessionLocal
from ..models import User
from .. import services

IMAGES = Path(__file__).resolve().parent.parent.parent / "data" / "images"
# Telegram re-uploads a file every time unless we reuse the id it returns for it.
_file_ids: dict[str, str] = {}


def image_path(name: str | None) -> Path | None:
    if not name:
        return None
    path = IMAGES / name
    return path if path.exists() else None


def photo_for(name: str | None):
    """A cached file_id when Telegram already knows the picture, otherwise the file itself."""
    path = image_path(name)
    if path is None:
        return None
    return _file_ids.get(name) or FSInputFile(path)


def remember_photo(name: str | None, message: Message | None) -> None:
    if name and message and message.photo:
        _file_ids.setdefault(name, message.photo[-1].file_id)

log = logging.getLogger(__name__)

ANCHOR_KEY = "anchor_msg_id"


async def edit(
    cq_or_msg: CallbackQuery | Message,
    text: str,
    kb: InlineKeyboardMarkup | None = None,
    image: str | None = None,
) -> Message | None:
    """Update the message under the pressed button in place — never send a new one.

    With `image`, the message becomes (or stays) a photo card: Telegram cannot turn a text message
    into a photo one, so that single case replaces the message instead of editing it.
    """
    msg = cq_or_msg.message if isinstance(cq_or_msg, CallbackQuery) else cq_or_msg
    if msg is None:
        return None
    has_photo = bool(msg.photo)
    photo = photo_for(image)
    try:
        if photo is not None and has_photo:
            sent = await msg.edit_media(InputMediaPhoto(media=photo, caption=text), reply_markup=kb)
            remember_photo(image, sent if isinstance(sent, Message) else None)
            return sent
        if photo is not None and not has_photo:
            await delete_quietly(msg)
            sent = await msg.answer_photo(photo, caption=text, reply_markup=kb)
            remember_photo(image, sent)
            return sent
        if has_photo:
            return await msg.edit_caption(caption=text, reply_markup=kb)
        return await msg.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except TelegramBadRequest as ex:
        if "message is not modified" in str(ex):
            return msg
        log.warning("edit failed (%s), sending a new message", ex)
        if photo is not None:
            await delete_quietly(msg)
            sent = await msg.answer_photo(photo, caption=text, reply_markup=kb)
            remember_photo(image, sent)
            return sent
        return await msg.answer(text, reply_markup=kb, disable_web_page_preview=True)


async def edit_anchor(bot: Bot, chat_id: int, state: FSMContext, text: str, kb: InlineKeyboardMarkup | None = None) -> None:
    """Edit the 'anchor' message stored in FSM state (used when the user replied with text and we want to update
    the bot's message instead of spamming new ones)."""
    data = await state.get_data()
    mid = data.get(ANCHOR_KEY)
    if mid:
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=mid, reply_markup=kb, disable_web_page_preview=True)
            return
        except TelegramBadRequest as ex:
            if "message is not modified" in str(ex):
                return
    m = await bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
    await state.update_data({ANCHOR_KEY: m.message_id})


async def remember_anchor(state: FSMContext, msg: Message | None) -> None:
    if msg:
        await state.update_data({ANCHOR_KEY: msg.message_id})


async def delete_quietly(msg: Message) -> None:
    with contextlib.suppress(Exception):
        await msg.delete()


async def answer_cq(cq: CallbackQuery, text: str | None = None, alert: bool = False) -> None:
    with contextlib.suppress(Exception):
        await cq.answer(text, show_alert=alert)


def session():
    return SessionLocal()


async def load_user(s, tg) -> User:
    await services.get_or_create_user(s, tg.id, tg.username)
    await s.commit()
    return await services.get_user(s, tg.id)
