"""/start, rules, registration FSM and the main menu."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from ... import keyboards as kb
from ... import services, texts
from ...config import settings
from ...models import UserStatus
from .. import channels
from ..common import ANCHOR_KEY, answer_cq, delete_quietly, edit, edit_anchor, load_user, remember_anchor, session
from ..states import Registration

router = Router(name="start")


async def render_menu(s, user) -> tuple[str, object]:
    if user.status in (UserStatus.pending, UserStatus.rejected):
        # Not approved yet: no teams, no tasks — just the application status.
        return texts.pending_status(user), kb.pending_kb(user)
    my_points = await services.user_points(s, user.id)
    rows = await services.leaderboard(s)
    team_points = None
    rank = None
    if user.team_id:
        for i, r in enumerate(rows, 1):
            if r["team"].id == user.team_id:
                team_points, rank = r["points"], i
    cw = settings.current_week()
    ws = dict(await services.week_stats_for_user(s, user.id, cw.number)) if cw else {}
    ws["total_teams"] = len(rows)
    ws["approved_total"] = len(
        [x for x in await services.user_submissions(s, user.id) if x.status.value == "approved"]
    )
    pending = await services.pending_count(s) if user.is_admin else 0
    return texts.main_menu(user, my_points, team_points, rank, ws), kb.main_menu_kb(user, pending)


@router.message(CommandStart())
@router.message(Command("menu"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    if message.text and message.text.startswith("/start ") and "_" in message.text:
        return  # deep links are handled by their own routers
    await state.clear()
    async with session() as s:
        user = await load_user(s, message.from_user)
        if user.status == UserStatus.new:
            m = await message.answer(texts.welcome(user), reply_markup=kb.start_kb())
        else:
            text, markup = await render_menu(s, user)
            m = await message.answer(text, reply_markup=markup)
    await state.update_data({ANCHOR_KEY: m.message_id})


@router.callback_query(F.data == "start")
async def cb_start(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session() as s:
        user = await load_user(s, cq.from_user)
        if user.status == UserStatus.new:
            await edit(cq, texts.welcome(user), kb.start_kb())
        else:
            text, markup = await render_menu(s, user)
            await edit(cq, text, markup)
    await answer_cq(cq)


@router.callback_query(F.data == "menu")
async def cb_menu(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session() as s:
        user = await load_user(s, cq.from_user)
        if user.status == UserStatus.new:
            await edit(cq, texts.welcome(user), kb.start_kb())
        else:
            text, markup = await render_menu(s, user)
            m = await edit(cq, text, markup)
            await remember_anchor(state, m)
    await answer_cq(cq)


@router.callback_query(F.data == "rules")
async def cb_rules(cq: CallbackQuery) -> None:
    async with session() as s:
        user = await load_user(s, cq.from_user)
    await edit(cq, texts.RULES, kb.rules_kb(user.status != UserStatus.new))
    await answer_cq(cq)


@router.message(Command("rules"))
async def cmd_rules(message: Message) -> None:
    async with session() as s:
        user = await load_user(s, message.from_user)
    await message.answer(texts.RULES, reply_markup=kb.rules_kb(user.status != UserStatus.new))


# ---------- registration ----------

@router.callback_query(F.data == "reg:start")
async def reg_start(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await state.set_state(Registration.full_name)
    await state.update_data({ANCHOR_KEY: cq.message.message_id, "draft": {}})
    await edit(cq, texts.registration_step("full_name", {}), kb.reg_kb("full_name"))
    await answer_cq(cq)


@router.message(Registration.full_name, F.text)
async def reg_name(message: Message, state: FSMContext) -> None:
    name = " ".join(message.text.split())
    await delete_quietly(message)
    if len(name) < 3 or len(name) > 80:
        await edit_anchor(message.bot, message.chat.id, state, texts.registration_step("full_name", {}) + "\n\n⚠️ Введи фамилию и имя (от 3 символов).", kb.reg_kb("full_name"))
        return
    data = await state.get_data()
    draft = data.get("draft", {})
    draft["full_name"] = name
    await state.update_data(draft=draft)
    await state.set_state(Registration.department)
    await edit_anchor(message.bot, message.chat.id, state, texts.registration_step("department", draft), kb.reg_kb("department"))


@router.message(Registration.department, F.text)
async def reg_department(message: Message, state: FSMContext) -> None:
    await delete_quietly(message)
    data = await state.get_data()
    draft = data.get("draft", {})
    draft["department"] = " ".join(message.text.split())[:160]
    await state.update_data(draft=draft)
    await state.set_state(Registration.city)
    await edit_anchor(message.bot, message.chat.id, state, texts.registration_step("city", draft), kb.reg_kb("city"))


@router.message(Registration.city, F.text)
async def reg_city(message: Message, state: FSMContext) -> None:
    await delete_quietly(message)
    data = await state.get_data()
    draft = data.get("draft", {})
    draft["city"] = " ".join(message.text.split())[:80]
    await state.update_data(draft=draft)
    await state.set_state(Registration.confirm)
    await edit_anchor(message.bot, message.chat.id, state, texts.registration_step("confirm", draft), kb.reg_kb("confirm"))


@router.callback_query(F.data.startswith("reg:skip:"))
async def reg_skip(cq: CallbackQuery, state: FSMContext) -> None:
    step = cq.data.split(":")[-1]
    data = await state.get_data()
    draft = data.get("draft", {})
    if step == "department":
        await state.set_state(Registration.city)
        await edit(cq, texts.registration_step("city", draft), kb.reg_kb("city"))
    else:
        await state.set_state(Registration.confirm)
        await edit(cq, texts.registration_step("confirm", draft), kb.reg_kb("confirm"))
    await answer_cq(cq)


@router.callback_query(F.data == "reg:confirm")
async def reg_confirm(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    draft = data.get("draft", {})
    if not draft.get("full_name"):
        await answer_cq(cq, "Сначала введи имя", alert=True)
        return
    async with session() as s:
        user = await load_user(s, cq.from_user)
        if not await services.get_flag(s, "registration_open"):
            await answer_cq(cq, "Регистрация на марафон закрыта. Обратитесь к сотруднику P&C.", alert=True)
            return
        status = await services.register_user(s, user, draft["full_name"], draft.get("department"), draft.get("city"))
        await s.commit()
        user = await services.get_user(s, cq.from_user.id)
        if status == UserStatus.pending:
            # Publish the application card for P&C to accept or decline.
            await channels.post_registration(cq.bot, s, user)
            await s.commit()
            user = await services.get_user(s, cq.from_user.id)
        text, markup = await render_menu(s, user)
    await state.clear()
    await state.update_data({ANCHOR_KEY: cq.message.message_id})
    if status == UserStatus.pending:
        await edit(cq, "✅ <b>Анкета отправлена!</b>\n\n" + text, markup)
        await answer_cq(cq, "Заявка отправлена на модерацию")
    else:
        await edit(cq, "🎉 <b>Регистрация завершена!</b>\n\nТеперь выбери команду — без команды отчёты отправлять нельзя.\n\n" + text, markup)
        await answer_cq(cq, "Добро пожаловать в марафон! 🌱")
