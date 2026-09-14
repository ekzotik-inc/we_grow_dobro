"""Галерея: как задания выполнили коллеги.

Фотографии одной работы уходят альбомом — в Telegram они листаются прямо внутри
сообщения. Кнопки под ним переключают не фото, а работы.
"""
from __future__ import annotations

import contextlib
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto, InputMediaVideo

from ... import keyboards as kb
from ... import services, texts
from ...models import UserStatus
from ..common import ANCHOR_KEY, answer_cq, edit, load_user, session

log = logging.getLogger(__name__)
router = Router(name="gallery")

MSGS_KEY = "gal_msgs"


async def _clear(bot, chat_id: int, state: FSMContext) -> None:
    """Убрать прошлую работу: альбом и подпись под ним живут отдельными сообщениями."""
    data = await state.get_data()
    for mid in data.get(MSGS_KEY, []):
        with contextlib.suppress(Exception):
            await bot.delete_message(chat_id, mid)
    await state.update_data({MSGS_KEY: []})


async def _show(cq: CallbackQuery, state: FSMContext, index: int, week: int | None) -> None:
    async with session() as s:
        user = await load_user(s, cq.from_user)
        if user.status != UserStatus.registered:
            await answer_cq(cq, "Галерея откроется после подтверждения заявки.", alert=True)
            return
        items = await services.gallery_items(s, week)
        total = len(items)
        if not total:
            await _clear(cq.bot, cq.message.chat.id, state)
            await edit(cq, texts.gallery_empty(week), kb.gallery_kb(0, 0, week))
            await answer_cq(cq)
            return
        index = max(1, min(index, total))
        sub = items[index - 1]
        media = services.gallery_media(sub)
        caption = texts.gallery_caption(sub, index, total)

    await _clear(cq.bot, cq.message.chat.id, state)
    markup = kb.gallery_kb(index, total, week)
    sent_ids: list[int] = []
    try:
        if len(media) == 1:
            # Одно фото — подпись и кнопки помещаются в то же сообщение.
            item = media[0]
            send = cq.bot.send_video if item["type"] == "video" else cq.bot.send_photo
            m = await send(cq.message.chat.id, item["file_id"], caption=caption, reply_markup=markup)
            sent_ids.append(m.message_id)
        else:
            group = [
                (InputMediaVideo if f["type"] == "video" else InputMediaPhoto)(
                    media=f["file_id"], caption=caption if i == 0 else None
                )
                for i, f in enumerate(media)
            ]
            album = await cq.bot.send_media_group(cq.message.chat.id, group)
            sent_ids.extend(x.message_id for x in album)
            # У альбома не бывает своей клавиатуры — кнопки уходят отдельным сообщением.
            nav = await cq.message.answer(f"<i>Работа {index} из {total}</i>", reply_markup=markup)
            sent_ids.append(nav.message_id)
    except Exception as ex:  # noqa: BLE001
        log.warning("галерея: не отправить работу %s: %s", sub.id, ex)
        await answer_cq(cq, "Не получилось показать эту работу, попробуйте следующую.", alert=True)
        return

    await state.update_data({MSGS_KEY: sent_ids})
    await answer_cq(cq)


@router.callback_query(F.data == "gal")
async def cb_gallery(cq: CallbackQuery, state: FSMContext) -> None:
    """Первый экран: сколько работ и как листать."""
    await state.update_data({MSGS_KEY: (await state.get_data()).get(MSGS_KEY, [])})
    async with session() as s:
        user = await load_user(s, cq.from_user)
        if user.status != UserStatus.registered:
            await answer_cq(cq, "Галерея откроется после подтверждения заявки.", alert=True)
            return
        total = len(await services.gallery_items(s, None))
    if not total:
        await edit(cq, texts.gallery_empty(None), kb.gallery_kb(0, 0, None))
        await answer_cq(cq)
        return
    m = await edit(cq, texts.gallery_intro(total, None), kb.gallery_kb(0, total, None))
    await state.update_data({ANCHOR_KEY: m.message_id})
    await _show(cq, state, 1, None)


@router.callback_query(F.data.regexp(r"^gal:i:(\d+):(\d+)$"))
async def cb_gallery_item(cq: CallbackQuery, state: FSMContext) -> None:
    _, _, index, week = cq.data.split(":")
    await _show(cq, state, int(index), int(week) or None)


@router.callback_query(F.data.regexp(r"^gal:w:(\d+)$"))
async def cb_gallery_week(cq: CallbackQuery, state: FSMContext) -> None:
    week = int(cq.data.split(":")[2]) or None
    await _show(cq, state, 1, week)
