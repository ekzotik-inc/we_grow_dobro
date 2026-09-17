"""Команда /del — массовые действия владельца бота.

Два действия, которые вручную заняли бы полчаса: снять с марафона всех, кто ни разу
ничего не отправил, и расформировать самые малочисленные команды, раскидав людей по
остальным. Оба необратимы, поэтому каждое сначала показывает, что именно произойдёт.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ... import keyboards as kb
from ... import services, texts
from ...config import settings
from ..common import answer_cq, edit, session

log = logging.getLogger(__name__)
router = Router(name="cleanup")

DQ_REASON = "Не выполнено ни одного задания за марафон"


class IsOwner(BaseFilter):
    """Только владелец из ADMIN_IDS: массовые снятия и перетасовка команд — не про P&C."""

    async def __call__(self, event) -> bool:
        uid = event.from_user.id if event.from_user else 0
        return uid in settings.admin_ids


router.message.filter(IsOwner())
router.callback_query.filter(IsOwner())


async def _root_screen(s):
    inactive = await services.inactive_participants(s)
    donors, receivers = await services.smallest_teams(s)
    return (texts.cleanup_root(inactive, donors, receivers),
            kb.cleanup_kb(len(inactive), len(donors)))


@router.message(Command("del"))
async def cmd_del(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with session() as s:
        text, markup = await _root_screen(s)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "del")
async def cb_del(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session() as s:
        text, markup = await _root_screen(s)
    await edit(cq, text, markup)
    await answer_cq(cq)


# ---------- неактивные участники ----------

@router.callback_query(F.data == "del:inactive")
async def cb_inactive(cq: CallbackQuery) -> None:
    async with session() as s:
        users = await services.inactive_participants(s)
    if not users:
        await answer_cq(cq, "Неактивных нет.", alert=True)
        return
    await edit(cq, texts.cleanup_confirm_inactive(users), kb.cleanup_confirm_kb("inactive"))
    await answer_cq(cq)


@router.callback_query(F.data == "del:inactive_ok")
async def cb_inactive_ok(cq: CallbackQuery) -> None:
    await answer_cq(cq, "Снимаю…")
    async with session() as s:
        users = await services.inactive_participants(s)
        done = await services.bulk_disqualify(s, users, DQ_REASON, cq.from_user.id)
        await s.commit()
        targets = [u.tg_id for u in done]
    for tg_id in targets:
        try:
            await cq.bot.send_message(tg_id, texts.push_disqualified(DQ_REASON))
        except Exception as ex:  # noqa: BLE001
            log.warning("не удалось уведомить %s о дисквалификации: %s", tg_id, ex)
    async with session() as s:
        _, markup = await _root_screen(s)
    await edit(cq, texts.cleanup_done_inactive(len(done)), markup)


# ---------- перетасовка команд ----------

@router.callback_query(F.data == "del:merge")
async def cb_merge(cq: CallbackQuery) -> None:
    async with session() as s:
        donors, receivers = await services.smallest_teams(s)
    if not donors:
        await answer_cq(cq, "Расформировывать нечего.", alert=True)
        return
    await edit(cq, texts.cleanup_confirm_merge(donors, receivers), kb.cleanup_confirm_kb("merge"))
    await answer_cq(cq)


@router.callback_query(F.data == "del:merge_ok")
async def cb_merge_ok(cq: CallbackQuery) -> None:
    await answer_cq(cq, "Перераспределяю…")
    async with session() as s:
        info = await services.merge_smallest_teams(s, cq.from_user.id)
        await s.commit()
        # Уведомления собираем до закрытия сессии: объекты за её пределами уже не читаются.
        notes = [(item["user"].tg_id, item["from"]) for item in info["moved"]]
        fresh = {item["user"].tg_id: await services.get_user(s, item["user"].tg_id)
                 for item in info["moved"]}
    for tg_id, old_name in notes:
        user = fresh.get(tg_id)
        if user is None or user.team is None:
            continue
        try:
            await cq.bot.send_message(tg_id, texts.push_team_assigned(user, old_name),
                                      reply_markup=kb.back_kb("menu", "🏠 Меню"))
        except Exception as ex:  # noqa: BLE001
            log.warning("не удалось уведомить %s о переводе: %s", tg_id, ex)
    async with session() as s:
        _, markup = await _root_screen(s)
    await edit(cq, texts.cleanup_done_merge(info), markup)
