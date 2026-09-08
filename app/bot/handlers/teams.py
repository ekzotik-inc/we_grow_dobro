"""Teams: list, card, join/leave, create, leaderboard, help from P&C."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ... import keyboards as kb
from ... import services, texts
from ...config import settings
from ...models import UserStatus
from ..common import ANCHOR_KEY, answer_cq, delete_quietly, edit, edit_anchor, load_user, session
from ..states import HelpFlow, TeamCreate

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
    can_join = len(members) < settings.team_size and user.status == UserStatus.registered
    return texts.team_card(team, members, points, rank, upts, user), kb.team_card_kb(team, user, can_join)


@router.callback_query(F.data == "teams")
async def cb_teams(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session() as s:
        user = await load_user(s, cq.from_user)
        if user.status == UserStatus.new:
            await answer_cq(cq, "Сначала зарегистрируйся", alert=True)
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


@router.callback_query(F.data.regexp(r"^team:join:(\d+)$"))
async def cb_team_join(cq: CallbackQuery) -> None:
    team_id = int(cq.data.split(":")[2])
    async with session() as s:
        team = await services.get_team(s, team_id)
    if team is None:
        await answer_cq(cq, "Команда не найдена", alert=True)
        return
    await edit(
        cq,
        f"Вступить в команду <b>{texts.e(team.emoji)} {texts.e(team.name)}</b>?\n\n"
        "После старта марафона сменить команду можно будет только через сотрудника P&C.",
        kb.team_confirm_join_kb(team_id),
    )
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^team:join_ok:(\d+)$"))
async def cb_team_join_ok(cq: CallbackQuery) -> None:
    team_id = int(cq.data.split(":")[2])
    async with session() as s:
        user = await load_user(s, cq.from_user)
        try:
            team = await services.join_team(s, user, team_id)
            await s.commit()
        except services.ServiceError as ex:
            await answer_cq(cq, str(ex), alert=True)
            return
        user = await services.get_user(s, cq.from_user.id)
        text, markup = await render_team_card(s, team.id, user)
    await edit(cq, "✅ Вы вступили в команду!\n\n" + text, markup)
    await answer_cq(cq, f"Добро пожаловать в «{team.name}»!")


@router.callback_query(F.data.regexp(r"^team:leave:(\d+)$"))
async def cb_team_leave(cq: CallbackQuery) -> None:
    async with session() as s:
        user = await load_user(s, cq.from_user)
        try:
            await services.leave_team(s, user)
            await s.commit()
        except services.ServiceError as ex:
            await answer_cq(cq, str(ex), alert=True)
            return
        user = await services.get_user(s, cq.from_user.id)
        text, markup = await render_teams(s, user)
    await edit(cq, "🚪 Вы покинули команду.\n\n" + text, markup)
    await answer_cq(cq)


# ---------- create team ----------

@router.callback_query(F.data == "team:new")
async def cb_team_new(cq: CallbackQuery, state: FSMContext) -> None:
    async with session() as s:
        user = await load_user(s, cq.from_user)
    if user.team_id:
        await answer_cq(cq, "Вы уже в команде", alert=True)
        return
    await state.set_state(TeamCreate.name)
    await state.update_data({ANCHOR_KEY: cq.message.message_id})
    await edit(cq, "➕ <b>Новая команда</b>\n\nНапиши название команды сообщением в чат (2–40 символов).", kb.cancel_kb("teams"))
    await answer_cq(cq)


@router.message(TeamCreate.name, F.text)
async def team_name(message: Message, state: FSMContext) -> None:
    name = " ".join(message.text.split())[:40]
    await delete_quietly(message)
    if len(name) < 2:
        await edit_anchor(message.bot, message.chat.id, state, "⚠️ Название слишком короткое. Напиши название команды (2–40 символов).", kb.cancel_kb("teams"))
        return
    await state.update_data(team_name=name)
    await state.set_state(TeamCreate.emoji)
    await edit_anchor(message.bot, message.chat.id, state, f"Команда <b>{texts.e(name)}</b>. Выбери эмодзи-символ команды:", kb.team_emoji_kb())


@router.callback_query(TeamCreate.emoji, F.data.startswith("team:emoji:"))
async def team_emoji(cq: CallbackQuery, state: FSMContext) -> None:
    emoji = cq.data.split(":", 2)[2]
    data = await state.get_data()
    name = data.get("team_name", "")
    async with session() as s:
        user = await load_user(s, cq.from_user)
        try:
            team = await services.create_team(s, user, name, emoji)
            await s.commit()
        except services.ServiceError as ex:
            await answer_cq(cq, str(ex), alert=True)
            await state.set_state(TeamCreate.name)
            await edit(cq, f"⚠️ {texts.e(str(ex))}\n\nНапиши другое название команды:", kb.cancel_kb("teams"))
            return
        user = await services.get_user(s, cq.from_user.id)
        text, markup = await render_team_card(s, team.id, user)
    await state.clear()
    await edit(cq, "🎉 Команда создана! Пригласи коллег — пусть выберут её в списке команд.\n\n" + text, markup)
    await answer_cq(cq)


# ---------- help from P&C ----------

@router.callback_query(F.data == "help")
async def cb_help(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await edit(
        cq,
        f"🆘 <b>Помощь P&C</b>\n\nЕсли не получается выбрать команду или есть вопрос по марафону — выбери пункт ниже, "
        f"и {texts.e(settings.pc_contact)} свяжется с тобой.",
        kb.help_kb(),
    )
    await answer_cq(cq)


async def _notify_admins(bot, s, text: str) -> int:
    n = 0
    for admin_id in await services.list_admin_tg_ids(s):
        try:
            await bot.send_message(admin_id, text)
            n += 1
        except Exception as ex:  # noqa: BLE001
            log.warning("notify admin %s failed: %s", admin_id, ex)
    return n


@router.callback_query(F.data == "help:team")
async def cb_help_team(cq: CallbackQuery) -> None:
    async with session() as s:
        user = await load_user(s, cq.from_user)
        if user.status == UserStatus.new:
            await answer_cq(cq, "Сначала зарегистрируйся", alert=True)
            return
        uname = f" (@{user.username})" if user.username else ""
        await _notify_admins(
            cq.bot,
            s,
            f"🆘 <b>Запрос на распределение в команду</b>\n\n{texts.e(user.display_name)}{texts.e(uname)}"
            + (f" · {texts.e(user.department)}" if user.department else "")
            + f"\nТекущая команда: {texts.e(user.team.name) if user.team else '—'}\n\n"
            f"Распределить: /admin → Участники → {texts.e(user.display_name)} → «Перевести в команду».",
        )
    await edit(cq, "✅ Запрос отправлен сотруднику P&C. Тебя распределят в команду и уведомят в этом чате.", kb.back_kb())
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
        await _notify_admins(cq_bot := message.bot, s, f"❓ <b>Вопрос от {texts.e(user.display_name)}{texts.e(uname)}</b> (tg id {user.tg_id}):\n\n{texts.e(message.text)}")
    await delete_quietly(message)
    await state.clear()
    await edit_anchor(cq_bot, message.chat.id, state, "✅ Вопрос передан сотруднику P&C. Ответ придёт в этот чат.", kb.back_kb())
