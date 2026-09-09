"""Скрытая команда /addresult — ручная корректировка результатов.

Раздел только для владельца бота: сотрудники P&C и участники сюда не попадают.
Здесь видно всё, что участники отправляли, и можно изменить баллы, отменить уже
проверенный отчёт или обнулить результаты участника либо всей команды.
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
from ...models import SubmissionStatus, UserStatus
from ..common import ANCHOR_KEY, answer_cq, delete_quietly, edit, edit_anchor, load_user, session
from ..states import ResultsFlow

log = logging.getLogger(__name__)
router = Router(name="results")


class IsOwner(BaseFilter):
    """Только владелец бота из ADMIN_IDS.

    Сотрудники P&C сюда не допускаются намеренно: они проверяют отчёты, а исправлять
    начисленное и обнулять результаты может лишь владелец.
    """

    async def __call__(self, event) -> bool:
        uid = event.from_user.id if event.from_user else 0
        return uid in settings.admin_ids


router.message.filter(IsOwner())
router.callback_query.filter(IsOwner())


async def _notify(bot, user, text: str) -> None:
    """Участник должен понимать, откуда изменение в его счёте."""
    try:
        await bot.send_message(user.tg_id, text, reply_markup=kb.back_kb("menu", "🏠 Меню"))
    except Exception as ex:  # noqa: BLE001
        log.warning("не смог сообщить участнику %s: %s", user.tg_id, ex)


async def _render_root(s):
    users = await services.list_participants(s)
    points = await services.user_points_map(s)
    approved = 0
    for u in users:
        approved += len([x for x in await services.user_submissions(s, u.id)
                         if x.status == SubmissionStatus.approved])
    total = sum(points.values())
    with_points = len([1 for v in points.values() if v])
    return texts.results_root(total, approved, with_points), kb.results_root_kb()


@router.message(Command("addresult"))
async def cmd_results(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with session() as s:
        await load_user(s, message.from_user)
        text, markup = await _render_root(s)
    m = await message.answer(text, reply_markup=markup)
    await state.update_data({ANCHOR_KEY: m.message_id})


@router.callback_query(F.data == "res")
async def cb_root(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session() as s:
        text, markup = await _render_root(s)
    await edit(cq, text, markup)
    await answer_cq(cq)


# ---------- участники ----------

@router.callback_query(F.data.regexp(r"^res:users:(\d+)$"))
async def cb_users(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    page = int(cq.data.split(":")[2])
    async with session() as s:
        users = [u for u in await services.list_participants(s) if u.status != UserStatus.new]
        points = await services.user_points_map(s)
    users.sort(key=lambda u: -points.get(u.id, 0))
    await edit(cq, f"👤 <b>Участники</b>\n<i>всего {len(users)}, отсортированы по баллам</i>",
               kb.results_users_kb(users, points, page))
    await answer_cq(cq)


@router.callback_query(F.data == "res:find")
async def cb_find(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(ResultsFlow.search)
    await state.update_data({ANCHOR_KEY: cq.message.message_id})
    await edit(cq, "🔍 Напиши фамилию, имя, @username, телефон или Telegram id участника:",
               kb.results_back_kb("res:users:0"))
    await answer_cq(cq)


@router.message(ResultsFlow.search, F.text)
async def find_text(message: Message, state: FSMContext) -> None:
    q = message.text.strip().lstrip("@").casefold()
    digits = "".join(c for c in q if c.isdigit())
    await delete_quietly(message)
    async with session() as s:
        users = await services.list_participants(s)
        points = await services.user_points_map(s)
    found = [
        u for u in users
        if q in (u.full_name or "").casefold()
        or q in (u.username or "").casefold()
        or q == str(u.tg_id)
        or (digits and len(digits) >= 5 and digits in "".join(c for c in (u.phone or "") if c.isdigit()))
    ]
    await state.clear()
    if not found:
        await edit_anchor(message.bot, message.chat.id, state,
                          f"🔍 По запросу «{texts.e(message.text)}» никого не нашлось.",
                          kb.results_back_kb("res:users:0"))
        return
    await edit_anchor(message.bot, message.chat.id, state, f"🔍 Найдено: {len(found)}",
                      kb.results_users_kb(found, points, 0))


async def _render_user(cq_or_msg, state: FSMContext, uid: int) -> None:
    async with session() as s:
        user = await services.get_user_by_id(s, uid)
        if user is None:
            await answer_cq(cq_or_msg, "Участник не найден", alert=True) if isinstance(cq_or_msg, CallbackQuery) else None
            return
        subs = await services.user_submissions(s, uid)
        total = await services.user_points(s, uid)
        text = texts.results_user(user, subs, total)
        markup = kb.results_user_kb(user, [x for x in subs if x.status == SubmissionStatus.approved])
    if isinstance(cq_or_msg, CallbackQuery):
        await edit(cq_or_msg, text, markup)
    else:
        await edit_anchor(cq_or_msg.bot, cq_or_msg.chat.id, state, text, markup)


@router.callback_query(F.data.regexp(r"^res:user:(\d+)$"))
async def cb_user(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _render_user(cq, state, int(cq.data.split(":")[2]))
    await answer_cq(cq)


# ---------- изменение баллов ----------

@router.callback_query(F.data.regexp(r"^res:(add|sub):(\d+)$"))
async def cb_amount(cq: CallbackQuery, state: FSMContext) -> None:
    _, kind, uid = cq.data.split(":")
    async with session() as s:
        user = await services.get_user_by_id(s, int(uid))
    if user is None:
        await answer_cq(cq, "Участник не найден", alert=True)
        return
    await state.set_state(ResultsFlow.amount)
    await state.update_data({ANCHOR_KEY: cq.message.message_id, "uid": int(uid), "sign": 1 if kind == "add" else -1})
    word = "начислить" if kind == "add" else "списать"
    await edit(cq, f"✍️ <b>Сколько баллов {word}?</b>\n<i>{texts.e(user.display_name)}</i>\n\n"
                   "Напиши число сообщением, например <code>200</code>.",
               kb.results_back_kb(f"res:user:{uid}"))
    await answer_cq(cq)


@router.message(ResultsFlow.amount, F.text)
async def amount_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await delete_quietly(message)
    raw = "".join(c for c in message.text if c.isdigit())
    if not raw or int(raw) == 0:
        await edit_anchor(message.bot, message.chat.id, state,
                          "⚠️ Нужно число больше нуля. Например: <code>200</code>",
                          kb.results_back_kb(f"res:user:{data.get('uid')}"))
        return
    await state.update_data(amount=int(raw))
    await state.set_state(ResultsFlow.reason)
    sign = data.get("sign", 1)
    word = "начисляем" if sign > 0 else "списываем"
    await edit_anchor(message.bot, message.chat.id, state,
                      f"✍️ <b>{int(raw)} б. — {word}</b>\n\n"
                      "Напиши причину сообщением: участник увидит её в уведомлении.",
                      kb.results_back_kb(f"res:user:{data.get('uid')}"))


@router.message(ResultsFlow.reason, F.text)
async def reason_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    reason = message.text.strip()[:200]
    await delete_quietly(message)
    uid, amount, sign = data.get("uid"), data.get("amount", 0), data.get("sign", 1)
    delta = amount * sign
    async with session() as s:
        user = await services.get_user_by_id(s, uid)
        if user is None:
            await state.clear()
            await edit_anchor(message.bot, message.chat.id, state, "Участник не найден.", kb.results_back_kb())
            return
        try:
            total = await services.adjust_points(s, user, delta, reason, message.from_user.id)
        except services.ServiceError as ex:
            await edit_anchor(message.bot, message.chat.id, state, f"⚠️ {texts.e(str(ex))}",
                              kb.results_back_kb(f"res:user:{uid}"))
            return
        await s.commit()
        tg_id, name = user.tg_id, user.display_name
    await _notify(message.bot, type("U", (), {"tg_id": tg_id})(), texts.push_points_adjusted(delta, reason, total))
    await state.clear()
    sign_word = "начислено" if delta > 0 else "списано"
    await edit_anchor(message.bot, message.chat.id, state,
                      f"✅ <b>{abs(delta)} б. {sign_word}</b>\n{texts.e(name)} — теперь {texts.num(total)} б.\n\n"
                      f"<i>{texts.e(reason)}</i>\n\nУчастник уведомлён.",
                      kb.results_back_kb(f"res:user:{uid}"))


# ---------- отмена зачёта ----------

@router.callback_query(F.data.regexp(r"^res:revoke:(\d+)$"))
async def cb_revoke(cq: CallbackQuery, state: FSMContext) -> None:
    sub_id = int(cq.data.split(":")[2])
    async with session() as s:
        sub = await services.get_submission(s, sub_id)
        if sub is None or sub.status != SubmissionStatus.approved:
            await answer_cq(cq, "Этот отчёт уже не зачтён", alert=True)
            return
        title, points, uid = sub.task.title, sub.points_awarded, sub.user_id
    await state.set_state(ResultsFlow.revoke_reason)
    await state.update_data({ANCHOR_KEY: cq.message.message_id, "sub_id": sub_id, "uid": uid})
    await edit(cq, f"↩️ <b>Отменить зачёт</b>\n<i>{texts.e(title)} · {points} б.</i>\n\n"
                   "Напиши причину сообщением — участник её увидит и сможет переделать отчёт.",
               kb.results_back_kb(f"res:user:{uid}"))
    await answer_cq(cq)


@router.message(ResultsFlow.revoke_reason, F.text)
async def revoke_reason_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    reason = message.text.strip()[:300]
    await delete_quietly(message)
    async with session() as s:
        sub = await services.get_submission(s, data.get("sub_id"))
        if sub is None:
            await state.clear()
            await edit_anchor(message.bot, message.chat.id, state, "Отчёт не найден.", kb.results_back_kb())
            return
        try:
            returned = await services.revoke_review(s, sub, message.from_user.id, reason)
        except services.ServiceError as ex:
            await edit_anchor(message.bot, message.chat.id, state, f"⚠️ {texts.e(str(ex))}",
                              kb.results_back_kb(f"res:user:{data.get('uid')}"))
            return
        await s.commit()
        sub = await services.get_submission(s, sub.id)
        title, tg_id, uid = sub.task.title, sub.user.tg_id, sub.user_id
    await _notify(message.bot, type("U", (), {"tg_id": tg_id})(), texts.push_review_revoked(title, returned, reason))
    await state.clear()
    await edit_anchor(message.bot, message.chat.id, state,
                      f"↩️ <b>Зачёт отменён</b>\n{texts.e(title)} — снято {returned} б.\n\nУчастник уведомлён.",
                      kb.results_back_kb(f"res:user:{uid}"))


# ---------- обнуление результатов ----------

@router.callback_query(F.data.regexp(r"^res:wipe_user:(\d+)$"))
async def cb_wipe_user(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    uid = int(cq.data.split(":")[2])
    async with session() as s:
        user = await services.get_user_by_id(s, uid)
        if user is None:
            await answer_cq(cq, "Участник не найден", alert=True)
            return
        subs = [x for x in await services.user_submissions(s, uid) if x.status != SubmissionStatus.cancelled]
        total = await services.user_points(s, uid)
        name = user.display_name
    await edit(cq, f"🗑 <b>Обнулить результаты участника?</b>\n<i>{texts.e(name)}</i>\n\n"
                   + texts.row("📤", "Отчётов удалится", str(len(subs))) + "\n"
                   + texts.row("⭐", "Снимется баллов", texts.num(total)) + "\n\n"
                   "Действие необратимое. Участник останется в марафоне и сможет отправить отчёты заново.",
               kb.results_confirm_kb("wipe_user", uid, f"res:user:{uid}"))
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^res:wipe_user_ok:(\d+)$"))
async def cb_wipe_user_ok(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    uid = int(cq.data.split(":")[2])
    async with session() as s:
        user = await services.get_user_by_id(s, uid)
        if user is None:
            await answer_cq(cq, "Участник не найден", alert=True)
            return
        info = await services.clear_user_results(s, user, cq.from_user.id)
        await s.commit()
        tg_id, name = user.tg_id, user.display_name
    if info["points"]:
        await _notify(cq.bot, type("U", (), {"tg_id": tg_id})(), texts.push_results_cleared(info["points"]))
    await edit(cq, f"🗑 <b>Результаты обнулены</b>\n<i>{texts.e(name)}</i>\n\n"
                   + texts.row("📤", "Удалено отчётов", str(info["submissions"])) + "\n"
                   + texts.row("⭐", "Снято баллов", texts.num(info["points"])),
               kb.results_back_kb(f"res:user:{uid}"))
    await answer_cq(cq, "Готово")


# ---------- команды ----------

@router.callback_query(F.data == "res:teams")
async def cb_teams(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session() as s:
        rows = await services.leaderboard(s)
    await edit(cq, "🏷 <b>Команды</b>\n<i>выберите, чьи результаты смотреть</i>", kb.results_teams_kb(rows))
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^res:team:(\d+)$"))
async def cb_team(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    team_id = int(cq.data.split(":")[2])
    async with session() as s:
        team = await services.get_team(s, team_id)
        if team is None:
            await answer_cq(cq, "Команда не найдена", alert=True)
            return
        points = await services.user_points_map(s)
        members = services.team_active_members(team)
        total = next((r["points"] for r in await services.leaderboard(s) if r["team"].id == team_id), 0)
        text = texts.results_team(team, members, points, total)
        markup = kb.results_team_kb(team, members)
    await edit(cq, text, markup)
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^res:wipe_team:(\d+)$"))
async def cb_wipe_team(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    team_id = int(cq.data.split(":")[2])
    async with session() as s:
        team = await services.get_team(s, team_id)
        if team is None:
            await answer_cq(cq, "Команда не найдена", alert=True)
            return
        members = services.team_active_members(team)
        points = await services.user_points_map(s)
        total = sum(points.get(m.id, 0) for m in members)
        name = f"{team.emoji} {team.name}"
    await edit(cq, f"🗑 <b>Обнулить результаты всей команды?</b>\n<i>{texts.e(name)}</i>\n\n"
                   + texts.row("👥", "Участников", str(len(members))) + "\n"
                   + texts.row("⭐", "Снимется баллов", texts.num(total)) + "\n\n"
                   "У каждого удалятся все отчёты. Действие необратимое.",
               kb.results_confirm_kb("wipe_team", team_id, f"res:team:{team_id}"))
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^res:wipe_team_ok:(\d+)$"))
async def cb_wipe_team_ok(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    team_id = int(cq.data.split(":")[2])
    async with session() as s:
        team = await services.get_team(s, team_id)
        if team is None:
            await answer_cq(cq, "Команда не найдена", alert=True)
            return
        members = [(m.tg_id, await services.user_points(s, m.id)) for m in services.team_active_members(team)]
        info = await services.clear_team_results(s, team, cq.from_user.id)
        await s.commit()
        name = f"{team.emoji} {team.name}"
    for tg_id, had in members:
        if had:
            await _notify(cq.bot, type("U", (), {"tg_id": tg_id})(), texts.push_results_cleared(had))
    await edit(cq, f"🗑 <b>Результаты команды обнулены</b>\n<i>{texts.e(name)}</i>\n\n"
                   + texts.row("👥", "Участников", str(info["members"])) + "\n"
                   + texts.row("📤", "Удалено отчётов", str(info["submissions"])) + "\n"
                   + texts.row("⭐", "Снято баллов", texts.num(info["points"])),
               kb.results_back_kb(f"res:team:{team_id}"))
    await answer_cq(cq, "Готово")


# ---------- журнал ----------

@router.callback_query(F.data == "res:log")
async def cb_log(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session() as s:
        entries = await services.recent_points_log(s)
    await edit(cq, texts.results_log(entries), kb.results_back_kb())
    await answer_cq(cq)
