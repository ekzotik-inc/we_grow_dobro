"""Moderation of participant applications — works both in the registration channel and in DMs.

A press on «Принять»/«Отклонить» edits the same card in place (editMessageText) instead of
posting anything new, so the channel stays a clean decision log.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ... import keyboards as kb
from ... import services, texts
from ...models import UserStatus
from .. import channels
from ..common import ANCHOR_KEY, answer_cq, delete_quietly, edit, edit_anchor, session
from ..states import AdminFlow
from .admin import IsAdmin

log = logging.getLogger(__name__)
router = Router(name="moderation")

# Карточки заявок и отчётов живут в каналах, где их видят все участники канала.
# Решение принимают только владелец бота и сотрудники P&C — фильтр тот же, что у панели.
router.callback_query.filter(IsAdmin())
router.message.filter(IsAdmin())


async def is_admin(tg_id: int) -> bool:
    from ...config import settings

    if settings.is_admin(tg_id):
        return True
    async with session() as s:
        u = await services.get_user(s, tg_id)
        return bool(u and u.is_admin)


async def _guard(cq: CallbackQuery) -> bool:
    """Anyone can see a channel post; only P&C may press its buttons."""
    if await is_admin(cq.from_user.id):
        return True
    await answer_cq(cq, "Только для сотрудников P&C 🔒", alert=True)
    return False


def _in_channel(cq: CallbackQuery) -> bool:
    return bool(cq.message and cq.message.chat.type == "channel")


async def _refresh_card(cq: CallbackQuery, user) -> None:
    """Redraw the application card under the pressed button."""
    in_channel = _in_channel(cq)
    markup = kb.moderation_kb(user.id) if user.status == UserStatus.pending else (None if in_channel else kb.admin_user_kb(user))
    await edit(cq, channels.emoji.strip(texts.registration_channel_card(user)), markup)


@router.callback_query(F.data.regexp(r"^mod:card:(\d+)$"))
async def cb_mod_card(cq: CallbackQuery) -> None:
    if not await _guard(cq):
        return
    uid = int(cq.data.split(":")[2])
    async with session() as s:
        user = await services.get_user_by_id(s, uid)
    if user is None:
        await answer_cq(cq, "Участник не найден", alert=True)
        return
    await _refresh_card(cq, user)
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^mod:ok:(\d+)$"))
async def cb_mod_approve(cq: CallbackQuery) -> None:
    if not await _guard(cq):
        return
    uid = int(cq.data.split(":")[2])
    async with session() as s:
        user = await services.get_user_by_id(s, uid)
        if user is None:
            await answer_cq(cq, "Участник не найден", alert=True)
            return
        try:
            await services.approve_user(s, user, cq.from_user.id)
            await s.commit()
        except services.ServiceError as ex:
            await answer_cq(cq, str(ex), alert=True)
            return
        user = await services.get_user_by_id(s, uid)
        # Keep the channel card in sync when the decision was made from a DM (and vice versa).
        if not _in_channel(cq):
            await channels.update_registration_post(cq.bot, s, user)
            await s.commit()
    try:
        await cq.bot.send_message(user.tg_id, texts.push_approved(user), reply_markup=kb.back_kb("menu", "🏠 Меню"))
    except Exception as ex:  # noqa: BLE001
        log.warning("notify approved user %s failed: %s", user.tg_id, ex)
    await _refresh_card(cq, user)
    await answer_cq(cq, "Участник принят ✅")


@router.callback_query(F.data.regexp(r"^mod:rej:(\d+)$"))
async def cb_mod_reject(cq: CallbackQuery) -> None:
    if not await _guard(cq):
        return
    uid = int(cq.data.split(":")[2])
    me = await cq.bot.get_me()
    await edit(
        cq,
        "❌ <b>Отклонить заявку</b>\n\nВыбери причину — участник её увидит:",
        kb.mod_reject_reason_kb(uid, _in_channel(cq), me.username),
    )
    await answer_cq(cq)


async def do_mod_reject(bot, uid: int, reason: str, actor_tg_id: int):
    """Apply the rejection, notify the applicant, sync the channel card. Returns the user."""
    async with session() as s:
        user = await services.get_user_by_id(s, uid)
        if user is None:
            return None
        try:
            await services.reject_user(s, user, actor_tg_id, reason)
            await s.commit()
        except services.ServiceError:
            return await services.get_user_by_id(s, uid)
        user = await services.get_user_by_id(s, uid)
        await channels.update_registration_post(bot, s, user)
        await s.commit()
    try:
        await bot.send_message(
            user.tg_id,
            f"❌ <b>Заявка на участие отклонена.</b>\nПричина: <i>{texts.e(reason)}</i>\n\n"
            "Если это ошибка — напиши сотруднику P&C.",
            reply_markup=kb.back_kb("help", "🆘 Помощь P&C"),
        )
    except Exception as ex:  # noqa: BLE001
        log.warning("notify rejected user %s failed: %s", user.tg_id, ex)
    return user


@router.callback_query(F.data.regexp(r"^mod:rej_r:(\d+):(\w+)$"))
async def cb_mod_reject_reason(cq: CallbackQuery) -> None:
    if not await _guard(cq):
        return
    _, _, uid, code = cq.data.split(":")
    reason = kb.MOD_REJECT_REASONS.get(code, "Заявка отклонена сотрудником P&C.")
    user = await do_mod_reject(cq.bot, int(uid), reason, cq.from_user.id)
    if user is None:
        await answer_cq(cq, "Участник не найден", alert=True)
        return
    await _refresh_card(cq, user)
    await answer_cq(cq, "Заявка отклонена")


@router.callback_query(F.data.regexp(r"^mod:rej_custom:(\d+)$"))
async def cb_mod_reject_custom(cq: CallbackQuery, state: FSMContext) -> None:
    if not await _guard(cq):
        return
    uid = int(cq.data.split(":")[2])
    await state.set_state(AdminFlow.mod_reject_reason)
    await state.update_data({ANCHOR_KEY: cq.message.message_id, "mod_uid": uid})
    await edit(cq, "✍️ Напиши причину отклонения заявки сообщением:", kb.cancel_kb(f"mod:card:{uid}"))
    await answer_cq(cq)


@router.message(AdminFlow.mod_reject_reason, F.text)
async def mod_reject_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    uid = data.get("mod_uid")
    await delete_quietly(message)
    user = await do_mod_reject(message.bot, uid, message.text.strip()[:300], message.from_user.id)
    await state.clear()
    if user is None:
        return
    await edit_anchor(message.bot, message.chat.id, state, channels.emoji.strip(texts.registration_channel_card(user)), kb.admin_user_kb(user))


@router.message(Command("start"), F.text.startswith("/start modrej_"))
async def deep_link_mod_reject(message: Message, state: FSMContext) -> None:
    """Deep link from the channel button: type a free-form rejection reason in the bot's DM."""
    if not await is_admin(message.from_user.id):
        return
    uid = int(message.text.split("modrej_", 1)[1].strip() or 0)
    async with session() as s:
        user = await services.get_user_by_id(s, uid)
    if user is None:
        await message.answer("Заявка не найдена.")
        return
    await state.set_state(AdminFlow.mod_reject_reason)
    await state.update_data(mod_uid=uid)
    m = await message.answer(
        f"✍️ Причина отклонения заявки <b>{texts.e(user.display_name)}</b>? Напиши сообщением:",
        reply_markup=kb.cancel_kb("adm"),
    )
    await state.update_data({ANCHOR_KEY: m.message_id})
