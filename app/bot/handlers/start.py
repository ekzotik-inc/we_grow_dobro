"""/start, rules, registration FSM and the main menu."""
from __future__ import annotations

import contextlib

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
# Перехватчик «сообщение никому не подошло» подключается последним (см. setup_routers),
# иначе он забрал бы текст, который ждут другие шаги: помощь, отчёты, админ-панель.
fallback_router = Router(name="fallback")


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
    cw = services.current_week()
    ws = dict(await services.week_stats_for_user(s, user.id, cw.number)) if cw else {}
    ws["total_teams"] = len(rows)
    ws["approved_total"] = len(
        [x for x in await services.user_submissions(s, user.id) if x.status.value == "approved"]
    )
    ws["my_rank"], ws["total_users"] = await services.participant_rank(s, user.id)
    pending = await services.pending_count(s) if settings.is_admin(user.tg_id) else 0
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
    if _looks_like_phone(name):
        # Иначе присланный заранее номер записался бы именем, а телефон бот спросил бы снова —
        # именно так и терялась первая попытка.
        phone = _clean_phone(name)
        if phone:
            async with session() as s:
                user = await load_user(s, message.from_user)
                await services.save_draft(s, user, phone=phone)
                await s.commit()
            draft["phone"] = phone
            await state.update_data(draft=draft)
        await edit_anchor(message.bot, message.chat.id, state,
                          texts.registration_step("full_name", draft)
                          + "\n\n⚠️ Это похоже на номер — я его сохранил. Сначала напиши имя и фамилию.",
                          kb.reg_kb("full_name"))
        return
    draft["full_name"] = name
    async with session() as s:
        user = await load_user(s, message.from_user)
        await services.save_draft(s, user, full_name=name)
        await s.commit()
        known_phone = user.phone
    await state.update_data(draft=draft)
    if known_phone:
        # Номер участник прислал раньше — второй раз не спрашиваем.
        draft["phone"] = known_phone
        await state.update_data(draft=draft)
        await _phone_accepted(message, state, known_phone)
        return
    await state.set_state(Registration.phone)
    await edit_anchor(message.bot, message.chat.id, state, texts.registration_step("phone", draft), kb.reg_kb("phone"))
    # A reply keyboard is the only way to offer Telegram's "share my number" button.
    prompt = await message.answer("👇 Кнопка «Поделиться номером» — для телефона. "
                                  "С компьютера просто напиши номер сообщением.",
                                  reply_markup=kb.phone_request_kb())
    await state.update_data(phone_prompt_id=prompt.message_id)


def _looks_like_phone(raw: str) -> bool:
    """Строка из цифр, плюса, скобок и дефисов — это номер, а не имя."""
    digits = [c for c in raw if c.isdigit()]
    extra = [c for c in raw if not c.isdigit() and c not in "+-() "]
    return len(digits) >= 9 and not extra


def _clean_phone(raw: str) -> str | None:
    """Привести номер к виду +998901234567. Местный номер без кода страны дополняем сами."""
    kept = "".join(c for c in raw if c.isdigit() or c == "+")
    digits = kept.replace("+", "")
    code = settings.phone_country_code
    if code and not kept.startswith("+") and len(digits) == 9:
        digits = code + digits
    if not 10 <= len(digits) <= 15:
        return None
    return f"+{digits}"


async def _drop_phone_keyboard(message: Message, state: FSMContext, note: str) -> None:
    """Снять клавиатуру «Поделиться номером».

    Снимается только отправкой сообщения с ReplyKeyboardRemove, и удалять его нельзя:
    Telegram не успевает применить снятие, и кнопка остаётся висеть в чате навсегда.
    """
    await message.answer(note, reply_markup=ReplyKeyboardRemove())
    data = await state.get_data()
    prompt_id = data.get("phone_prompt_id")
    if prompt_id:
        with contextlib.suppress(Exception):
            await message.bot.delete_message(message.chat.id, prompt_id)
        await state.update_data(phone_prompt_id=None)


async def _phone_accepted(message: Message, state: FSMContext, phone: str) -> None:
    data = await state.get_data()
    draft = data.get("draft", {})
    draft["phone"] = phone
    async with session() as s:
        user = await load_user(s, message.from_user)
        await services.save_draft(s, user, phone=phone)
        await s.commit()
        if not draft.get("full_name") and user.full_name:
            draft["full_name"] = user.full_name
    await state.update_data(draft=draft)
    await state.set_state(Registration.team)

    await _drop_phone_keyboard(message, state, f"✅ <b>Номер принят:</b> {texts.e(phone)}")

    async with session() as s:
        rows = await services.leaderboard(s)
    await edit_anchor(message.bot, message.chat.id, state,
                      texts.registration_step("team", draft), kb.reg_team_kb(rows))


@router.message(Registration.full_name, F.contact)
@router.message(Registration.phone, F.contact)
@router.message(Registration.team, F.contact)
@router.message(Registration.confirm, F.contact)
async def reg_phone_contact(message: Message, state: FSMContext) -> None:
    """Номер принимаем на любом шаге анкеты: участник часто делится им заранее."""
    phone = message.contact.phone_number if message.contact else ""
    await delete_quietly(message)
    if not phone:
        return
    phone = phone if phone.startswith("+") else f"+{phone}"
    async with session() as s:
        user = await load_user(s, message.from_user)
        await services.save_draft(s, user, phone=phone)
        await s.commit()
        has_name = bool(user.full_name)
    data = await state.get_data()
    draft = dict(data.get("draft", {}))
    draft["phone"] = phone
    await state.update_data(draft=draft)
    if not has_name:
        # Имя ещё не введено — остаёмся на первом шаге, но номер уже сохранён.
        await _drop_phone_keyboard(message, state, f"✅ <b>Номер принят:</b> {texts.e(phone)}")
        await edit_anchor(message.bot, message.chat.id, state,
                          texts.registration_step("full_name", draft)
                          + "\n\n✅ Номер сохранил. Осталось имя и фамилия.",
                          kb.reg_kb("full_name"))
        await state.set_state(Registration.full_name)
        return
    await _phone_accepted(message, state, phone)


@router.message(Registration.phone, F.text)
async def reg_phone_text(message: Message, state: FSMContext) -> None:
    phone = _clean_phone(message.text)
    await delete_quietly(message)
    if not phone:
        data = await state.get_data()
        await edit_anchor(message.bot, message.chat.id, state,
                          texts.registration_step("phone", data.get("draft", {}))
                          + "\n\n⚠️ Не похоже на номер. Пример: +998 90 123 45 67",
                          kb.reg_kb("phone"))
        return
    await _phone_accepted(message, state, phone)


async def _resume_registration(message: Message, state: FSMContext) -> bool:
    """Продолжить анкету после перезапуска бота.

    Состояние диалога живёт в памяти, а сервис засыпает и перезапускается. Поэтому, если
    сообщение пришло «в никуда», шаг восстанавливается по тому, что уже сохранено в базе.
    """
    async with session() as s:
        user = await load_user(s, message.from_user)
        if user.status != UserStatus.new:
            return False
        draft = services.draft_from_user(user)
        step = services.draft_step(user)
        rows = await services.leaderboard(s) if step == "team" else []

    await state.set_state({"full_name": Registration.full_name, "phone": Registration.phone,
                           "team": Registration.team}[step])
    await state.update_data(draft=draft)
    markup = kb.reg_team_kb(rows) if step == "team" else kb.reg_kb(step)
    m = await message.answer(texts.registration_step(step, draft), reply_markup=markup)
    await state.update_data({ANCHOR_KEY: m.message_id})
    if step == "phone":
        prompt = await message.answer("👇 Кнопка «Поделиться номером» — для телефона. "
                                  "С компьютера просто напиши номер сообщением.",
                                  reply_markup=kb.phone_request_kb())
        await state.update_data(phone_prompt_id=prompt.message_id)
    return True


@fallback_router.callback_query(F.data.startswith("adm:") | (F.data == "adm")
                               | F.data.startswith("mod:") | F.data.startswith("res"))
async def stray_admin_click(cq: CallbackQuery, state: FSMContext) -> None:
    """Старые сообщения с кнопкой панели остаются в чатах участников.

    Фильтр админ-роутера такие нажатия просто не пропускает, и кнопка выглядит сломанной,
    поэтому отвечаем вежливым отказом и возвращаем человека в его меню.
    """
    await answer_cq(cq, "Раздел доступен только сотрудникам P&amp;C.", alert=True)
    async with session() as s:
        user = await load_user(s, cq.from_user)
        text, markup = (await render_menu(s, user)) if user.status != UserStatus.new else (texts.welcome(user), kb.start_kb())
    await edit(cq, text, markup)


@fallback_router.message(F.photo | F.document | F.video)
async def stray_media(message: Message, state: FSMContext) -> None:
    """Фото или файл, которого никто не ждал.

    Обычно это продолжение отчёта после перезапуска сервиса: черновик есть в базе,
    а состояние диалога потеряно. Возвращаем участника в отчёт и передаём файл дальше,
    иначе он пропадает молча.
    """
    from .tasks import resume_draft, sub_file

    if await resume_draft(message, state):
        await sub_file(message, state)
        return
    async with session() as s:
        user = await load_user(s, message.from_user)
        registered = user.status == UserStatus.registered
    await delete_quietly(message)
    if registered:
        await message.answer(
            "📷 <b>Файл получен, но он ни к чему не привязан</b>\n\n"
            "Открой задание и нажми «Сделать и отправить отчёт» — там я приму его в нужный шаг.",
            reply_markup=kb.back_kb("tasks", "📋 Задания недели"),
        )
        return
    await _fallback_menu(message, state)


@fallback_router.message(F.contact)
async def stray_contact(message: Message, state: FSMContext) -> None:
    """Номер прислали, а бот его не ждал — не молчим, а продолжаем анкету."""
    phone = message.contact.phone_number if message.contact else ""
    await delete_quietly(message)
    async with session() as s:
        user = await load_user(s, message.from_user)
        if user.status == UserStatus.new and phone:
            await services.save_draft(s, user, phone=phone if phone.startswith("+") else f"+{phone}")
            await s.commit()
    if not await _resume_registration(message, state):
        await _fallback_menu(message, state)


async def _fallback_menu(message: Message, state: FSMContext) -> None:
    async with session() as s:
        user = await load_user(s, message.from_user)
        if user.status == UserStatus.new:
            m = await message.answer(texts.welcome(user), reply_markup=kb.start_kb())
        else:
            text, markup = await render_menu(s, user)
            m = await message.answer(text, reply_markup=markup)
    await state.update_data({ANCHOR_KEY: m.message_id})


@router.callback_query(Registration.team, F.data.regexp(r"^reg:team:(\d+)$"))
async def reg_team_pick(cq: CallbackQuery, state: FSMContext) -> None:
    team_id = int(cq.data.split(":")[2])
    data = await state.get_data()
    draft = data.get("draft", {})
    async with session() as s:
        team = await services.get_team(s, team_id) if team_id else None
    draft["team_id"] = team_id or None
    draft["team_name"] = f"{team.emoji} {team.name}" if team else "на усмотрение P&amp;C"
    await state.update_data(draft=draft)
    await state.set_state(Registration.confirm)
    await edit(cq, texts.registration_step("confirm", draft), kb.reg_kb("confirm"))
    await answer_cq(cq)


async def _cleanup_after_registration(cq: CallbackQuery, state: FSMContext) -> None:
    """Страховка: если клавиатура номера почему-то осталась, снимаем её при отправке заявки."""
    data = await state.get_data()
    if not data.get("phone_prompt_id"):
        return
    with contextlib.suppress(Exception):
        m = await cq.bot.send_message(cq.message.chat.id, "✅ Готово", reply_markup=ReplyKeyboardRemove())
        await cq.bot.delete_message(cq.message.chat.id, data["phone_prompt_id"])
        await state.update_data(phone_prompt_id=None)
        return m


@router.callback_query(F.data == "reg:confirm")
async def reg_confirm(cq: CallbackQuery, state: FSMContext) -> None:
    await _cleanup_after_registration(cq, state)
    data = await state.get_data()
    draft = data.get("draft", {})
    if not draft.get("full_name"):
        await answer_cq(cq, "Сначала введи имя", alert=True)
        return
    async with session() as s:
        user = await load_user(s, cq.from_user)
        if not await services.get_flag(s, "registration_open"):
            await answer_cq(cq, "Регистрация на марафон закрыта. Обратитесь к сотруднику P&amp;C.", alert=True)
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


@fallback_router.message(F.text & ~F.text.startswith("/"))
async def stray_text(message: Message, state: FSMContext) -> None:
    """Последний обработчик: сообщение, которое не подошло никуда.

    Обычно это продолжение анкеты после перезапуска сервиса. Молчать нельзя — участник
    решит, что бот сломался, поэтому либо возвращаем его в анкету, либо показываем меню.
    """
    async with session() as s:
        user = await load_user(s, message.from_user)
        new_user = user.status == UserStatus.new
        if new_user and services.draft_step(user) == "full_name":
            name = " ".join((message.text or "").split())
            if 3 <= len(name) <= 80:
                await services.save_draft(s, user, full_name=name)
                await s.commit()
        elif new_user and services.draft_step(user) == "phone":
            phone = _clean_phone(message.text or "")
            if phone:
                await services.save_draft(s, user, phone=phone)
                await s.commit()
    await delete_quietly(message)
    if not await _resume_registration(message, state):
        await _fallback_menu(message, state)
