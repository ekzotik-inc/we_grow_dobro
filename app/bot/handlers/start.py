"""/start, rules, registration FSM and the main menu."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

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
    ws["my_rank"], ws["total_users"] = await services.participant_rank(s, user.id)
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
    data = await state.get_data()
    draft = data.get("draft", {})
    if len(name) < 3 or len(name) > 80:
        await edit_anchor(message.bot, message.chat.id, state,
                          texts.registration_step("full_name", draft) + "\n\n⚠️ Нужны имя и фамилия, от 3 символов.",
                          kb.reg_kb("full_name"))
        return
    draft["full_name"] = name
    await state.update_data(draft=draft)
    await state.set_state(Registration.phone)
    await edit_anchor(message.bot, message.chat.id, state, texts.registration_step("phone", draft), kb.reg_kb("phone"))
    # A reply keyboard is the only way to offer Telegram's "share my number" button.
    prompt = await message.answer("👇", reply_markup=kb.phone_request_kb())
    await state.update_data(phone_prompt_id=prompt.message_id)


def _clean_phone(raw: str) -> str | None:
    kept = "".join(c for c in raw if c.isdigit() or c == "+")
    digits = kept.replace("+", "")
    if not 10 <= len(digits) <= 15:
        return None
    return f"+{digits}"


async def _phone_accepted(message: Message, state: FSMContext, phone: str) -> None:
    data = await state.get_data()
    draft = data.get("draft", {})
    draft["phone"] = phone
    await state.update_data(draft=draft)
    await state.set_state(Registration.team)
    # Take the reply keyboard away: the rest of the bot is inline only.
    closer = await message.answer("✓", reply_markup=ReplyKeyboardRemove())
    await delete_quietly(closer)
    async with session() as s:
        rows = await services.leaderboard(s)
    await edit_anchor(message.bot, message.chat.id, state,
                      texts.registration_step("team", draft), kb.reg_team_kb(rows))


@router.message(Registration.phone, F.contact)
async def reg_phone_contact(message: Message, state: FSMContext) -> None:
    phone = message.contact.phone_number
    await delete_quietly(message)
    await _phone_accepted(message, state, phone if phone.startswith("+") else f"+{phone}")


@router.message(Registration.phone, F.text)
async def reg_phone_text(message: Message, state: FSMContext) -> None:
    phone = _clean_phone(message.text)
    await delete_quietly(message)
    if not phone:
        data = await state.get_data()
        await edit_anchor(message.bot, message.chat.id, state,
                          texts.registration_step("phone", data.get("draft", {}))
                          + "\n\n⚠️ Не похоже на номер. Пример: +7 700 123 45 67",
                          kb.reg_kb("phone"))
        return
    await _phone_accepted(message, state, phone)


@router.callback_query(Registration.team, F.data.regexp(r"^reg:team:(\d+)$"))
async def reg_team_pick(cq: CallbackQuery, state: FSMContext) -> None:
    team_id = int(cq.data.split(":")[2])
    data = await state.get_data()
    draft = data.get("draft", {})
    async with session() as s:
        team = await services.get_team(s, team_id) if team_id else None
    draft["team_id"] = team_id or None
    draft["team_name"] = f"{team.emoji} {team.name}" if team else "на усмотрение P&C"
    await state.update_data(draft=draft)
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
        status = await services.register_user(
            s, user, draft["full_name"], draft.get("department"), draft.get("city"),
            phone=draft.get("phone"), wanted_team_id=draft.get("team_id"),
        )
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
