"""Posting and editing moderation cards in the two Telegram channels.

Registration channel: one card per application, buttons «Принять» / «Отклонить».
Results channel: media of the report plus a card with «Зачесть» / «Отклонить».
After a decision the card is edited in place (editMessageText) and the buttons disappear,
so the channel keeps a readable audit trail instead of duplicate messages.
"""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputMediaDocument, InputMediaPhoto, InputMediaVideo

from .. import emoji, services, texts
from ..models import Submission, User

log = logging.getLogger(__name__)


async def send_files(bot: Bot, chat_id: int, files: list[dict]) -> list[int]:
    """Send stored files as albums (max 10). Telegram forbids mixing documents with photos/videos."""
    ids: list[int] = []
    media = []
    for f in files[:10]:
        if f["type"] == "photo":
            media.append(InputMediaPhoto(media=f["file_id"]))
        elif f["type"] == "video":
            media.append(InputMediaVideo(media=f["file_id"]))
        else:
            media.append(InputMediaDocument(media=f["file_id"]))
    photos = [m for m in media if not isinstance(m, InputMediaDocument)]
    docs = [m for m in media if isinstance(m, InputMediaDocument)]
    for group in (photos, docs):
        if not group:
            continue
        try:
            if len(group) == 1:
                m = group[0]
                if isinstance(m, InputMediaPhoto):
                    sent = await bot.send_photo(chat_id, m.media)
                elif isinstance(m, InputMediaVideo):
                    sent = await bot.send_video(chat_id, m.media)
                else:
                    sent = await bot.send_document(chat_id, m.media)
                ids.append(sent.message_id)
            else:
                sent = await bot.send_media_group(chat_id, group)
                ids.extend(x.message_id for x in sent)
        except Exception as ex:  # noqa: BLE001
            log.warning("send_files to %s failed: %s", chat_id, ex)
    return ids


async def notify_pc(bot: Bot, s, text: str, kb=None, exclude: int | None = None) -> int:
    """Participants' questions reach P&C staff only — never the owner.

    `exclude` keeps a staff card out of the requester's own chat: an admin who asks for a team
    would otherwise get the internal instructions meant for the person handling the request.
    """
    n = 0
    for pc_id in await services.list_pc_tg_ids(s):
        if pc_id == exclude:
            continue
        try:
            await bot.send_message(pc_id, text, reply_markup=kb)
            n += 1
        except Exception as ex:  # noqa: BLE001
            log.warning("notify P&amp;C %s failed: %s", pc_id, ex)
    return n


async def notify_admins(bot: Bot, s, text: str, kb=None) -> int:
    n = 0
    for admin_id in await services.list_admin_tg_ids(s):
        try:
            await bot.send_message(admin_id, text, reply_markup=kb)
            n += 1
        except Exception as ex:  # noqa: BLE001
            log.warning("notify admin %s failed: %s", admin_id, ex)
    return n


# ---------- registration channel ----------

async def post_registration(bot: Bot, s, user: User) -> None:
    """Publish an application card. Falls back to admins' DMs when no channel is bound."""
    from .. import keyboards as kb

    # Telegram never accepts custom emoji in channels — send the plain characters there.
    text = emoji.strip(texts.registration_channel_card(user))
    if user.wanted_team_id:
        wanted = await services.get_team(s, user.wanted_team_id)
        if wanted:
            text += f"\n🌱 Просит команду: {wanted.emoji} {wanted.name}"
    else:
        text += "\n🌱 Команду просит подобрать P&amp;C"
    markup = kb.moderation_kb(user.id)
    chat_id = await services.get_channel_id(s, "reg_channel_id")
    if chat_id:
        try:
            msg = await bot.send_message(chat_id, text, reply_markup=markup)
            user.reg_message_id = msg.message_id
            await s.flush()
            return
        except Exception as ex:  # noqa: BLE001
            log.warning("post_registration to channel %s failed: %s", chat_id, ex)
    await notify_admins(bot, s, text, markup)


async def update_registration_post(bot: Bot, s, user: User) -> None:
    chat_id = await services.get_channel_id(s, "reg_channel_id")
    if not chat_id or not user.reg_message_id:
        return
    try:
        await bot.edit_message_text(
            emoji.strip(texts.registration_channel_card(user)),
            chat_id=chat_id,
            message_id=user.reg_message_id,
            reply_markup=None,
        )
    except TelegramBadRequest as ex:
        if "message is not modified" not in str(ex):
            log.warning("update_registration_post failed: %s", ex)
    except Exception as ex:  # noqa: BLE001
        log.warning("update_registration_post failed: %s", ex)


# ---------- results channel ----------

async def post_submission(bot: Bot, s, sub: Submission) -> None:
    """Publish the report: media first, then the review card with buttons."""
    from .. import keyboards as kb

    text = emoji.strip(texts.submission_channel_card(sub))
    markup = kb.channel_review_kb(sub)
    chat_id = await services.get_channel_id(s, "results_channel_id")
    if chat_id:
        try:
            media_ids = await send_files(bot, chat_id, sub.files or [])
            msg = await bot.send_message(chat_id, text, reply_markup=markup)
            sub.channel_media_ids = media_ids
            sub.channel_message_id = msg.message_id
            await s.flush()
            return
        except Exception as ex:  # noqa: BLE001
            log.warning("post_submission to channel %s failed: %s", chat_id, ex)
    pending = await services.pending_count(s)
    await notify_admins(bot, s, text + f"\n\nВ очереди: {pending}.", kb.channel_review_kb(sub))


async def update_submission_post(bot: Bot, s, sub: Submission) -> None:
    chat_id = await services.get_channel_id(s, "results_channel_id")
    if not chat_id or not sub.channel_message_id:
        return
    try:
        await bot.edit_message_text(
            emoji.strip(texts.submission_channel_card(sub)),
            chat_id=chat_id,
            message_id=sub.channel_message_id,
            reply_markup=None,
        )
    except TelegramBadRequest as ex:
        if "message is not modified" not in str(ex):
            log.warning("update_submission_post failed: %s", ex)
    except Exception as ex:  # noqa: BLE001
        log.warning("update_submission_post failed: %s", ex)
