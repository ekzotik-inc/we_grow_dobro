"""Teams: list, card, join/leave, create, leaderboard, help from P&C."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ... import keyboards as kb
from ... import services, texts
from ...models import UserStatus
from .. import channels
from ..common import ANCHOR_KEY, answer_cq, delete_quietly, edit, edit_anchor, load_user, session
from ..states import HelpFlow

log = logging.getLogger(__name__)
router = Router(name="teams")


async def render_teams(s, user):
    rows = await services.leaderboard(s)
    rows.sort(key=lambda r: r["team"].name.lower())
    return texts.teams_list(rows, user), kb.teams_kb(rows, user)


async def render_team_card(s, team_id: int, user):
    team = await services.get_team(s, team_id)
    if team is None:
        return None, None
    rows = await services.leaderboard(s)
    rank, points = None, 0
    for i, r in enumerate(rows, 1):
        if r["team"].id == team.id:
            rank, points = i, r["points"]
    members = services.team_active_members(team)
    upts = await services.user_points_map(s)
    return texts.team_card(team, members, points, rank, upts, user), kb.team_card_kb(team, user, False)


@router.callback_query(F.data == "teams")
async def cb_teams(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session() as s:
        user = await load_user(s, cq.from_user)
        if user.status == UserStatus.new:
            await answer_cq(cq, "Сначала зарегистрируйся", alert=True)
            return
        if user.status in (UserStatus.pending, UserStatus.rejected):
            await answer_cq(cq, "Команды откроются после подтверждения заявки сотрудником P&C.", alert=True)
            return
        text, markup = await render_teams(s, user)
    await edit(cq, text, markup)
    await answer_cq(cq)


@router.callback_query(F.data == "top")
async def cb_top(cq: CallbackQuery) -> None:
    async with session() as s:
        rows = await services.leaderboard(s)
    await edit(cq, texts.leaderboard_text(rows), kb.back_kb())
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^team:(\d+)$"))
async def cb_team_card(cq: CallbackQuery) -> None:
    team_id = int(cq.data.split(":")[1])
    async with session() as s:
        user = await load_user(s, cq.from_user)
        text, markup = await render_team_card(s, team_id, user)
    if text is None:
        await answer_cq(cq, "Команда не найдена", alert=True)
        return
    await edit(cq, text, markup)
    await answer_cq(cq)


# ---------- help from P&C ----------

@router.callback_query(F.data == "help")
async def cb_help(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit(cq, texts.help_screen(), kb.help_kb())
    await answer_cq(cq)


@router.callback_query(F.data == "help:team")
async def cb_help_team(cq: CallbackQuery) -> None:
    async with session() as s:
        user = await load_user(s, cq.from_user)
        if user.status == UserStatus.new:
            await answer_cq(cq, "Сначала зарегистрируйся", alert=True)
            return
        uname = f" (@{user.username})" if user.username else ""
        sent = await channels.notify_pc(
            cq.bot,
            s,
            f"🆘 <b>Запрос на распределение в команду</b>\n\n{texts.e(user.display_name)}{texts.e(uname)}"
            + (f" · {texts.e(user.department)}" if user.department else "")
            + f"\nТекущая команда: {texts.e(user.team.name) if user.team else '—'}\n\n"
            f"Распределить: /admin → Участники → {texts.e(user.display_name)} → «Перевести в команду».",
            exclude=cq.from_user.id,
        )
    await edit(cq, texts.help_team_sent(sent), kb.back_kb())
    await answer_cq(cq)


@router.callback_query(F.data == "help:other")
async def cb_help_other(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(HelpFlow.question)
    await state.update_data({ANCHOR_KEY: cq.message.message_id})
    await edit(cq, "✍️ Напиши свой вопрос одним сообщением — он будет передан сотруднику P&C.", kb.cancel_kb("menu"))
    await answer_cq(cq)


@router.message(HelpFlow.question, F.text)
async def help_question(message: Message, state: FSMContext) -> None:
    async with session() as s:
        user = await load_user(s, message.from_user)
        uname = f" (@{user.username})" if user.username else ""
        await channels.notify_pc(cq_bot := message.bot, s, f"❓ <b>Вопрос от {texts.e(user.display_name)}{texts.e(uname)}</b> (tg id {user.tg_id}):\n\n{texts.e(message.text)}")
    await delete_quietly(message)
    await state.clear()
    await edit_anchor(cq_bot, message.chat.id, state, "✅ Вопрос передан сотруднику P&C. Ответ придёт в этот чат.", kb.back_kb())
