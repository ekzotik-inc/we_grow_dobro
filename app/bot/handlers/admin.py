"""P&C panel: review queue, participants, teams, announcements, broadcast, stats, export."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from ... import keyboards as kb
from ... import services, texts
from ...config import settings
from ...export import export_xlsx
from ...models import Broadcast, SubmissionStatus, UserStatus
from .. import channels
from ..channels import send_files
from ..common import ANCHOR_KEY, answer_cq, delete_quietly, edit, edit_anchor, load_user, session
from ..states import AdminFlow, TeamCreate

log = logging.getLogger(__name__)
router = Router(name="admin")


class IsAdmin:
    async def __call__(self, event) -> bool:
        uid = event.from_user.id if event.from_user else 0
        if settings.is_admin(uid):
            return True
        async with session() as s:
            u = await services.get_user(s, uid)
            return bool(u and u.is_admin)


router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())


def in_channel(cq: CallbackQuery) -> bool:
    return bool(cq.message and cq.message.chat.type == "channel")


async def render_admin(s):
    pending = await services.pending_count(s)
    apps = await services.pending_users_count(s)
    cw = services.current_week()
    reg_ch = await services.get_channel_id(s, "reg_channel_id")
    res_ch = await services.get_channel_id(s, "results_channel_id")
    participants = len([u for u in await services.list_participants(s) if u.status == UserStatus.registered])
    lines = ["🛠 <b>Панель P&C</b>",
             "<i>" + (f"открыта неделя {cw.number}" if cw else "ни одна неделя не открыта") + "</i>", ""]
    lines.append(texts.rule("Требует внимания"))
    lines.append(texts.row("🙋", "Заявок на модерации", str(apps)))
    lines.append(texts.row("🔎", "Отчётов на проверке", str(pending)))
    lines.append("")
    lines.append(texts.rule("Марафон"))
    lines.append(texts.row("👤", "Принятых участников", str(participants)))
    lines.append("")
    lines.append(("✅" if reg_ch else "⚠️") + " Канал заявок " + (f"<code>{reg_ch}</code>" if reg_ch else "не подключён"))
    lines.append(("✅" if res_ch else "⚠️") + " Канал результатов " + (f"<code>{res_ch}</code>" if res_ch else "не подключён"))
    if not (reg_ch and res_ch):
        lines.append("\nБез канала карточки приходят всем P&C в личку. Подключить: «Каналы и настройки».")
    warning = texts.db_expiry_warning()
    if warning:
        lines.append("\n" + warning)
    return "\n".join(lines), kb.admin_menu_kb(pending, apps)


@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    await state.clear()
    async with session() as s:
        await load_user(s, message.from_user)
        text, markup = await render_admin(s)
    m = await message.answer(text, reply_markup=markup)
    await state.update_data({ANCHOR_KEY: m.message_id})


@router.callback_query(F.data == "adm")
async def cb_admin(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session() as s:
        text, markup = await render_admin(s)
    await edit(cq, text, markup)
    await answer_cq(cq)


# ---------- review queue ----------

async def _render_sub(cq: CallbackQuery, s, sub, idx: int | None = None, total: int | None = None) -> None:
    if idx is None:
        queue = await services.pending_submissions(s)
        ids = [x.id for x in queue]
        idx = ids.index(sub.id) + 1 if sub.id in ids else 0
        total = len(ids)
    if sub.status == SubmissionStatus.pending:
        await edit(cq, texts.submission_admin_card(sub, idx or None, total), kb.review_kb(sub, idx or 1, total or 1))
    else:
        await edit(cq, texts.submission_admin_card(sub), kb.back_kb("adm:queue", "🔎 К очереди"))


@router.callback_query(F.data.regexp(r"^adm:queue(?::(\d+))?$"))
async def cb_queue(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    parts = cq.data.split(":")
    pos = int(parts[2]) if len(parts) > 2 else 0
    async with session() as s:
        queue = await services.pending_submissions(s)
        if not queue:
            await edit(cq, "🔎 Очередь проверки пуста — все отчёты проверены ✅", kb.back_kb("adm", "🛠 Панель"))
            await answer_cq(cq)
            return
        pos = max(0, min(pos, len(queue) - 1))
        await _render_sub(cq, s, queue[pos], pos + 1, len(queue))
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^adm:sub:(\d+)$"))
async def cb_sub(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    sub_id = int(cq.data.split(":")[2])
    async with session() as s:
        sub = await services.get_submission(s, sub_id)
        if sub is None:
            await answer_cq(cq, "Отчёт не найден", alert=True)
            return
        await _render_sub(cq, s, sub)
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^adm:files:(\d+)$"))
async def cb_files(cq: CallbackQuery) -> None:
    sub_id = int(cq.data.split(":")[2])
    async with session() as s:
        sub = await services.get_submission(s, sub_id)
    if sub is None or not sub.files:
        await answer_cq(cq, "Файлов нет", alert=True)
        return
    await answer_cq(cq)
    await send_files(cq.bot, cq.message.chat.id, sub.files)
    # Move the card under the files so the buttons are next to what the reviewer looks at.
    await delete_quietly(cq.message)
    async with session() as s:
        queue = await services.pending_submissions(s)
        ids = [x.id for x in queue]
        idx = ids.index(sub.id) + 1 if sub.id in ids else 1
        await cq.message.answer(texts.submission_admin_card(sub, idx, len(ids)), reply_markup=kb.review_kb(sub, idx, len(ids)))


async def _notify_user(bot, sub, approved: bool) -> None:
    if approved:
        text = (
            f"✅ <b>{texts.e(sub.task.title)} — зачтено</b>\n"
            f"+{sub.points_awarded} б. команде"
            + (f"\n\n{texts.e(sub.review_comment)}" if sub.review_comment else "")
            + "\n\n" + texts.voice(f"Спасибо за это дело {texts.plain('heart')}")
        )
    else:
        text = (
            f"❌ <b>{texts.e(sub.task.title)} — пока не зачтено</b>\n"
            f"{texts.e(sub.review_comment or 'условия зачёта выполнены не полностью')}\n\n"
            + texts.voice("Ничего страшного — доснимай, что просят, и отправляй снова. "
                          "Пока неделя идёт, попыток сколько угодно.")
        )
    try:
        await bot.send_message(sub.user.tg_id, text, reply_markup=kb.back_kb("menu", "🏠 Меню"))
    except Exception as ex:  # noqa: BLE001
        log.warning("notify user %s failed: %s", sub.user.tg_id, ex)


@router.callback_query(F.data.regexp(r"^adm:ok:(\d+)$"))
async def cb_approve(cq: CallbackQuery) -> None:
    sub_id = int(cq.data.split(":")[2])
    async with session() as s:
        sub = await services.get_submission(s, sub_id)
        if sub is None:
            await answer_cq(cq, "Не найдено", alert=True)
            return
        try:
            await services.review_submission(s, sub, True, cq.from_user.id)
            await s.commit()
        except services.ServiceError as ex:
            await answer_cq(cq, str(ex), alert=True)
            return
        sub = await services.get_submission(s, sub_id)
        await _notify_user(cq.bot, sub, True)
        if in_channel(cq):
            # In the channel the card itself is the UI: rewrite it and drop the buttons.
            await edit(cq, channels.emoji.strip(texts.submission_channel_card(sub)), None)
            sub.channel_message_id = cq.message.message_id
            await s.commit()
            await answer_cq(cq, f"Зачтено +{sub.points_awarded}")
            return
        await channels.update_submission_post(cq.bot, s, sub)
        await s.commit()
        queue = await services.pending_submissions(s)
        if queue:
            await _render_sub(cq, s, queue[0], 1, len(queue))
        else:
            await edit(cq, f"✅ Отчёт #{sub.id} зачтён (+{sub.points_awarded}).\n\nОчередь пуста.", kb.back_kb("adm", "🛠 Панель"))
    await answer_cq(cq, f"Зачтено +{sub.points_awarded}")


@router.callback_query(F.data.regexp(r"^adm:rej:(\d+)$"))
async def cb_reject(cq: CallbackQuery) -> None:
    sub_id = int(cq.data.split(":")[2])
    me = await cq.bot.get_me()
    await edit(
        cq,
        f"❌ <b>Отклонить отчёт #{sub_id}</b>\n\nВыбери причину — она будет отправлена участнику:",
        kb.reject_reason_kb(sub_id, in_channel(cq), me.username),
    )
    await answer_cq(cq)


async def _do_reject(cq_or_msg, state: FSMContext, sub_id: int, reason: str, actor_id: int, channel_card: bool = False) -> None:
    async with session() as s:
        sub = await services.get_submission(s, sub_id)
        if sub is None:
            return
        try:
            await services.review_submission(s, sub, False, actor_id, reason)
            await s.commit()
        except services.ServiceError as ex:
            if isinstance(cq_or_msg, CallbackQuery):
                await answer_cq(cq_or_msg, str(ex), alert=True)
            return
        sub = await services.get_submission(s, sub_id)
        bot = cq_or_msg.bot
        await _notify_user(bot, sub, False)
        if channel_card:
            sub.channel_message_id = cq_or_msg.message.message_id
            await s.commit()
            await edit(cq_or_msg, channels.emoji.strip(texts.submission_channel_card(sub)), None)
            return
        await channels.update_submission_post(bot, s, sub)
        await s.commit()
        queue = await services.pending_submissions(s)
        if queue:
            nxt = queue[0]
            text, markup = texts.submission_admin_card(nxt, 1, len(queue)), kb.review_kb(nxt, 1, len(queue))
        else:
            text, markup = f"❌ Отчёт #{sub.id} отклонён.\n\nОчередь пуста.", kb.back_kb("adm", "🛠 Панель")
    if isinstance(cq_or_msg, CallbackQuery):
        await edit(cq_or_msg, text, markup)
    else:
        await edit_anchor(bot, cq_or_msg.chat.id, state, text, markup)


@router.callback_query(F.data.regexp(r"^adm:rej_r:(\d+):(\w+)$"))
async def cb_reject_reason(cq: CallbackQuery, state: FSMContext) -> None:
    _, _, sub_id, code = cq.data.split(":")
    await _do_reject(cq, state, int(sub_id), kb.REJECT_REASONS.get(code, "Условия зачёта не выполнены."), cq.from_user.id, in_channel(cq))
    await answer_cq(cq, "Отклонено")


@router.callback_query(F.data.regexp(r"^adm:rej_custom:(\d+)$"))
async def cb_reject_custom(cq: CallbackQuery, state: FSMContext) -> None:
    sub_id = int(cq.data.split(":")[2])
    await state.set_state(AdminFlow.reject_reason)
    await state.update_data({ANCHOR_KEY: cq.message.message_id, "sub_id": sub_id})
    await edit(cq, f"✍️ Напиши причину отклонения отчёта #{sub_id} сообщением:", kb.cancel_kb(f"adm:sub:{sub_id}"))
    await answer_cq(cq)


@router.message(AdminFlow.reject_reason, F.text)
async def reject_custom_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    sub_id = data["sub_id"]
    await delete_quietly(message)
    await _do_reject(message, state, sub_id, message.text.strip()[:500], message.from_user.id)
    await state.clear()
    await state.update_data({ANCHOR_KEY: data.get(ANCHOR_KEY)})


@router.callback_query(F.data.regexp(r"^adm:card:(\d+)$"))
async def cb_channel_card(cq: CallbackQuery) -> None:
    """Redraw a report card in the results channel (used by «Назад» there)."""
    sub_id = int(cq.data.split(":")[2])
    async with session() as s:
        sub = await services.get_submission(s, sub_id)
    if sub is None:
        await answer_cq(cq, "Отчёт не найден", alert=True)
        return
    markup = kb.channel_review_kb(sub) if sub.status == SubmissionStatus.pending else None
    await edit(cq, channels.emoji.strip(texts.submission_channel_card(sub)), markup)
    await answer_cq(cq)


@router.message(Command("start"), F.text.startswith("/start subrej_"))
async def deep_link_sub_reject(message: Message, state: FSMContext) -> None:
    """Deep link from the results channel: type a free-form rejection reason in the bot's DM."""
    sub_id = int(message.text.split("subrej_", 1)[1].strip() or 0)
    async with session() as s:
        sub = await services.get_submission(s, sub_id)
    if sub is None:
        await message.answer("Отчёт не найден.")
        return
    await state.set_state(AdminFlow.reject_reason)
    await state.update_data(sub_id=sub_id)
    m = await message.answer(
        f"✍️ Причина отклонения отчёта #{sub_id} от <b>{texts.e(sub.user.display_name)}</b>? Напиши сообщением:",
        reply_markup=kb.cancel_kb("adm"),
    )
    await state.update_data({ANCHOR_KEY: m.message_id})


# ---------- participants ----------

USER_FILTERS = {
    "all": "Все",
    "no_team": "Без команды",
    "pending": "На модерации",
    "dq": "Дисквалифицированные",
}


def _filter_users(users, flt: str):
    if flt == "no_team":
        return [u for u in users if not u.team_id and u.status == UserStatus.registered]
    if flt == "pending":
        return [u for u in users if u.status == UserStatus.pending]
    if flt == "dq":
        return [u for u in users if u.status == UserStatus.disqualified]
    return users


@router.callback_query(F.data.regexp(r"^adm:users:(\d+)(?::(\w+))?$"))
async def cb_users(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    parts = cq.data.split(":")
    page = int(parts[2])
    flt = parts[3] if len(parts) > 3 else "all"
    async with session() as s:
        users = await services.list_participants(s)
    shown = _filter_users(users, flt)
    counts = {k: len(_filter_users(users, k)) for k in USER_FILTERS}
    head = (
        f"👥 <b>Участники</b> — всего {len(users)}\n"
        f"Фильтр: <b>{USER_FILTERS[flt]}</b> ({len(shown)})\n\n"
        "🆕 не завершил анкету · ⏳ на модерации · ❌ отклонён · 🚫 дисквалифицирован · ❔ без команды"
    )
    await edit(cq, head, kb.users_list_kb(shown, page, flt=flt, counts=counts))
    await answer_cq(cq)


@router.callback_query(F.data == "adm:user_search")
async def cb_user_search(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFlow.user_search)
    await state.update_data({ANCHOR_KEY: cq.message.message_id})
    await edit(cq, "🔍 Напиши фамилию, имя или @username участника:", kb.cancel_kb("adm:users:0"))
    await answer_cq(cq)


@router.message(AdminFlow.user_search, F.text)
async def user_search_text(message: Message, state: FSMContext) -> None:
    q = message.text.strip().lstrip("@").casefold()
    await delete_quietly(message)
    async with session() as s:
        users = await services.list_participants(s)
    found = [u for u in users if q in (u.full_name or "").casefold() or q in (u.username or "").casefold() or q == str(u.tg_id)]
    await state.clear()
    if not found:
        await edit_anchor(message.bot, message.chat.id, state, f"🔍 По запросу «{texts.e(message.text)}» никого не нашлось.", kb.back_kb("adm:users:0", "👥 Участники"))
        return
    await edit_anchor(message.bot, message.chat.id, state, f"🔍 Найдено: {len(found)}", kb.users_list_kb(found, 0))


async def _user_card(s, u) -> str:
    pts = await services.user_points(s, u.id)
    subs = await services.user_submissions(s, u.id)
    lines = [f"👤 <b>{texts.e(u.display_name)}</b>" + (f" (@{texts.e(u.username)})" if u.username else ""), f"<code>{u.tg_id}</code>"]
    if u.department:
        lines.append(f"🏢 {texts.e(u.department)}")
    if u.city:
        lines.append(f"📍 {texts.e(u.city)}")
    lines.append(f"👥 Команда: {texts.e(u.team.emoji + ' ' + u.team.name) if u.team else '— (без команды)'}")
    lines.append(f"⭐ Баллы: {pts} · {texts.USER_STATUS_TEXT[u.status]}")
    if u.disqualified_reason:
        lines.append(f"🚫 Причина: {texts.e(u.disqualified_reason)}")
    lines.append("")
    for x in subs:
        if x.status == SubmissionStatus.cancelled:
            continue
        lines.append(f"{texts.STATUS_ICON[x.status]} нед.{x.week} №{x.task.code} {texts.e(x.task.title[:40])} — {texts.STATUS_LABEL[x.status]}" + (f" +{x.points_awarded}" if x.points_awarded else ""))
    return "\n".join(lines)


@router.callback_query(F.data.regexp(r"^adm:user:(\d+)$"))
async def cb_user(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    uid = int(cq.data.split(":")[2])
    async with session() as s:
        u = await services.get_user_by_id(s, uid)
        if u is None:
            await answer_cq(cq, "Не найден", alert=True)
            return
        text = await _user_card(s, u)
    await edit(cq, text, kb.admin_user_kb(u))
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^adm:dq:(\d+)$"))
async def cb_dq(cq: CallbackQuery, state: FSMContext) -> None:
    uid = int(cq.data.split(":")[2])
    async with session() as s:
        u = await services.get_user_by_id(s, uid)
    await state.set_state(AdminFlow.dq_reason)
    await state.update_data({ANCHOR_KEY: cq.message.message_id, "uid": uid})
    await edit(cq, f"🚫 <b>Дисквалификация {texts.e(u.display_name)}</b>\n\nНапиши причину сообщением (участник её увидит). Его баллы перестанут учитываться в командном зачёте.", kb.cancel_kb(f"adm:user:{uid}"))
    await answer_cq(cq)


@router.message(AdminFlow.dq_reason, F.text)
async def dq_reason(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    uid = data["uid"]
    reason = message.text.strip()[:300]
    await delete_quietly(message)
    async with session() as s:
        u = await services.get_user_by_id(s, uid)
        await services.disqualify(s, u, reason, message.from_user.id)
        await s.commit()
        u = await services.get_user_by_id(s, uid)
        text = await _user_card(s, u)
    try:
        await message.bot.send_message(u.tg_id, texts.push_disqualified(reason))
    except Exception:  # noqa: BLE001
        pass
    await state.clear()
    await edit_anchor(message.bot, message.chat.id, state, "🚫 Участник дисквалифицирован.\n\n" + text, kb.admin_user_kb(u))


@router.callback_query(F.data.regexp(r"^adm:del:(\d+)$"))
async def cb_user_delete(cq: CallbackQuery, state: FSMContext) -> None:
    """Полное удаление участника — сначала показываем, что именно исчезнет."""
    await state.clear()
    uid = int(cq.data.split(":")[2])
    async with session() as s:
        u = await services.get_user_by_id(s, uid)
        if u is None:
            await answer_cq(cq, "Участник не найден", alert=True)
            return
        subs = len(await services.user_submissions(s, u.id))
        points = await services.user_points(s, u.id)
        text = texts.user_delete_confirm(u, subs, points)
    await edit(cq, text, kb.user_delete_confirm_kb(u))
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^adm:del_ok:(\d+)$"))
async def cb_user_delete_ok(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    uid = int(cq.data.split(":")[2])
    async with session() as s:
        u = await services.get_user_by_id(s, uid)
        if u is None:
            await answer_cq(cq, "Участник не найден", alert=True)
            return
        if u.tg_id == cq.from_user.id:
            await answer_cq(cq, "Себя удалить нельзя.", alert=True)
            return
        info = await services.delete_user(s, u)
        await s.commit()
    # Участник должен понимать, что произошло, и знать, что может вернуться.
    try:
        await cq.bot.send_message(info["tg_id"], texts.push_deleted())
    except Exception:  # noqa: BLE001
        pass
    await edit(cq, texts.user_deleted(info), kb.back_kb("adm:users:0", "⬅️ Участники"))
    await answer_cq(cq, "Участник удалён")


@router.callback_query(F.data.regexp(r"^adm:reinstate:(\d+)$"))
async def cb_reinstate(cq: CallbackQuery) -> None:
    uid = int(cq.data.split(":")[2])
    async with session() as s:
        u = await services.get_user_by_id(s, uid)
        await services.reinstate(s, u, cq.from_user.id)
        await s.commit()
        u = await services.get_user_by_id(s, uid)
        text = await _user_card(s, u)
    try:
        await cq.bot.send_message(u.tg_id, texts.push_reinstated())
    except Exception:  # noqa: BLE001
        pass
    await edit(cq, "♻️ Участник восстановлен.\n\n" + text, kb.admin_user_kb(u))
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^adm:move:(\d+)$"))
async def cb_move(cq: CallbackQuery) -> None:
    uid = int(cq.data.split(":")[2])
    async with session() as s:
        u = await services.get_user_by_id(s, uid)
        teams = await services.list_teams(s)
    await edit(cq, f"🔀 Перевести <b>{texts.e(u.display_name)}</b> в команду:", kb.move_team_kb(u, teams))
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^adm:move_to:(\d+):(\d+)$"))
async def cb_move_to(cq: CallbackQuery) -> None:
    _, _, uid, tid = cq.data.split(":")
    async with session() as s:
        u = await services.get_user_by_id(s, int(uid))
        old_team = u.team.name if u and u.team else None
        try:
            await services.move_user_to_team(s, u, int(tid) or None)
            await s.commit()
        except services.ServiceError as ex:
            await answer_cq(cq, str(ex), alert=True)
            return
        u = await services.get_user_by_id(s, int(uid))
        text = await _user_card(s, u)
    try:
        if u.team:
            await cq.bot.send_message(u.tg_id, texts.push_team_assigned(u, old_team), reply_markup=kb.back_kb("menu", "🏠 Меню"))
        else:
            await cq.bot.send_message(u.tg_id, texts.push_team_removed(), reply_markup=kb.back_kb("menu", "🏠 Меню"))
    except Exception:  # noqa: BLE001
        pass
    await edit(cq, "✅ Готово.\n\n" + text, kb.admin_user_kb(u))
    await answer_cq(cq)


@router.callback_query(F.data == "adm:teams")
async def cb_adm_teams(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session() as s:
        rows = await services.leaderboard(s)
    await edit(cq, texts.leaderboard_text(rows), kb.admin_teams_kb(rows))
    await answer_cq(cq)


@router.callback_query(F.data == "adm:team_new")
async def cb_team_new(cq: CallbackQuery, state: FSMContext) -> None:
    """Teams are created here only — participants pick from the list at sign-up."""
    await state.set_state(TeamCreate.name)
    await state.update_data({ANCHOR_KEY: cq.message.message_id})
    await edit(cq, "➕ <b>Новая команда</b>\n\nНапиши название сообщением (2–40 символов).", kb.cancel_kb("adm:teams"))
    await answer_cq(cq)


@router.message(TeamCreate.name, F.text)
async def team_new_name(message: Message, state: FSMContext) -> None:
    name = " ".join(message.text.split())[:40]
    await delete_quietly(message)
    if len(name) < 2:
        await edit_anchor(message.bot, message.chat.id, state, "⚠️ Слишком коротко. Напиши название команды:", kb.cancel_kb("adm:teams"))
        return
    await state.update_data(team_name=name)
    await state.set_state(TeamCreate.emoji)
    await edit_anchor(message.bot, message.chat.id, state, f"Команда <b>{texts.e(name)}</b>. Выбери символ:", kb.team_emoji_kb())


@router.callback_query(TeamCreate.emoji, F.data.startswith("team:emoji:"))
async def team_new_emoji(cq: CallbackQuery, state: FSMContext) -> None:
    emoji_char = cq.data.split(":", 2)[2]
    data = await state.get_data()
    async with session() as s:
        try:
            await services.create_team(s, None, data.get("team_name", ""), emoji_char)
            await s.commit()
        except services.ServiceError as ex:
            await answer_cq(cq, str(ex), alert=True)
            await state.set_state(TeamCreate.name)
            await edit(cq, f"⚠️ {texts.e(str(ex))}\n\nНапиши другое название:", kb.cancel_kb("adm:teams"))
            return
        rows = await services.leaderboard(s)
    await state.clear()
    await edit(cq, "✅ Команда создана.\n\n" + texts.leaderboard_text(rows), kb.admin_teams_kb(rows))
    await answer_cq(cq)


# ---------- announcements / broadcast ----------

async def send_to_users(bot, s, users, text: str, kind: str, image: str | None = None) -> int:
    """Deliver a message to an explicit list of users, throttled below Telegram's limit."""
    from ..common import photo_for, remember_photo

    n = 0
    for u in users:
        try:
            photo = photo_for(image)
            if photo is not None:
                sent = await bot.send_photo(u.tg_id, photo, caption=text, reply_markup=kb.back_kb("menu", "🏠 Меню"))
                remember_photo(image, sent)
            else:
                await bot.send_message(u.tg_id, text, reply_markup=kb.back_kb("menu", "🏠 Меню"))
            n += 1
        except Exception as ex:  # noqa: BLE001
            log.warning("broadcast to %s failed: %s", u.tg_id, ex)
        await asyncio.sleep(0.05)  # ~20 msg/s
    s.add(Broadcast(kind=kind, recipients=n))
    await s.commit()
    return n


async def broadcast(bot, s, text: str, kind: str, user_filter=None, image: str | None = None) -> int:
    """Send to all approved participants (optionally filtered). Used by announcements and the scheduler."""
    users = [u for u in await services.list_participants(s) if u.status == UserStatus.registered]
    if user_filter:
        users = [u for u in users if user_filter(u)]
    return await send_to_users(bot, s, users, text, kind, image)


@router.callback_query(F.data == "adm:announce")
async def cb_announce(cq: CallbackQuery) -> None:
    await edit(cq, "📣 Разослать анонс заданий недели всем участникам? Автоматическая рассылка тоже настроена (см. расписание), эта кнопка — для ручного повтора.", kb.announce_kb())
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^adm:announce_ok:(\d)$"))
async def cb_announce_ok(cq: CallbackQuery) -> None:
    week = int(cq.data.split(":")[2])
    await answer_cq(cq, "Рассылаю…")
    async with session() as s:
        tasks = await services.list_tasks(s, week)
        n = await broadcast(cq.bot, s, texts.week_announce(week, tasks), f"week_announce:{week}:manual", image=f"week{week}.png")
    await edit(cq, f"📣 Анонс недели {week} отправлен {n} участникам.", kb.back_kb("adm", "🛠 Панель"))


@router.callback_query(F.data.regexp(r"^adm:bcast$"))
async def cb_bcast(cq: CallbackQuery, state: FSMContext) -> None:
    """Step 1: pick the audience segment."""
    await state.clear()
    async with session() as s:
        counts = {}
        for code, _, _ in services.SEGMENTS:
            if code in ("lt_n_week", "team"):
                continue
            counts[code] = len(await services.segment_users(s, code))
    lines = ["✉️ <b>Рассылка</b>", "", "Выбери, кому отправить сообщение:", ""]
    for code, title, hint in services.SEGMENTS:
        n = counts.get(code)
        lines.append(f"• <b>{texts.e(title)}</b>" + (f" — {n}" if n is not None else "") + f"\n  <i>{texts.e(hint)}</i>")
    await edit(cq, "\n".join(lines), kb.segments_kb(counts))
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^adm:seg_pick:(\w+)$"))
async def cb_seg_pick(cq: CallbackQuery) -> None:
    """Segments that need a parameter: «< N заданий» and a specific team."""
    code = cq.data.split(":")[2]
    if code == "lt_n_week":
        await edit(cq, "Сколько заданий должно быть отправлено <b>меньше</b> за текущую неделю?", kb.segment_n_kb())
    else:
        async with session() as s:
            teams = await services.list_teams(s)
        if not teams:
            await answer_cq(cq, "Команд пока нет", alert=True)
            return
        await edit(cq, "Выбери команду-получателя:", kb.segment_team_kb(teams))
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^adm:seg:(\w+):(\w*)$"))
async def cb_seg(cq: CallbackQuery, state: FSMContext) -> None:
    """Step 2: segment chosen — ask for the message text."""
    _, _, code, arg = cq.data.split(":")
    arg = arg or None
    async with session() as s:
        users = await services.segment_users(s, code, arg)
        label = services.segment_label(code, arg)
        if code == "team" and arg:
            team = await services.get_team(s, int(arg))
            label = f"Команда {team.emoji} {team.name}" if team else label
    if not users:
        await answer_cq(cq, "В этом сегменте сейчас никого нет", alert=True)
        return
    await state.set_state(AdminFlow.broadcast)
    await state.update_data({ANCHOR_KEY: cq.message.message_id, "seg_code": code, "seg_arg": arg, "seg_label": label})
    preview = "\n".join(f"• {texts.e(u.display_name)}" for u in users[:15])
    more = f"\n… и ещё {len(users) - 15}" if len(users) > 15 else ""
    await edit(
        cq,
        f"✉️ <b>Сегмент: {texts.e(label)}</b>\nПолучателей: <b>{len(users)}</b>\n\n{preview}{more}\n\n"
        "Напиши текст рассылки сообщением (поддерживается HTML-разметка Telegram).",
        kb.cancel_kb("adm:bcast"),
    )
    await answer_cq(cq)


@router.message(AdminFlow.broadcast, F.text)
async def bcast_text(message: Message, state: FSMContext) -> None:
    """Step 3: preview with the live recipient count."""
    data = await state.get_data()
    await state.update_data(bcast_text=message.html_text)
    await delete_quietly(message)
    async with session() as s:
        users = await services.segment_users(s, data.get("seg_code", "all"), data.get("seg_arg"))
    await edit_anchor(
        message.bot,
        message.chat.id,
        state,
        texts.broadcast_preview(data.get("seg_label", "Все участники"), len(users), message.html_text),
        kb.bcast_confirm_kb(),
    )


@router.callback_query(AdminFlow.broadcast, F.data == "adm:bcast_ok")
async def cb_bcast_ok(cq: CallbackQuery, state: FSMContext) -> None:
    """Step 4: send."""
    data = await state.get_data()
    text = data.get("bcast_text", "")
    code, arg, label = data.get("seg_code", "all"), data.get("seg_arg"), data.get("seg_label", "Все")
    await state.clear()
    if not text:
        await answer_cq(cq, "Текст рассылки пуст", alert=True)
        return
    await answer_cq(cq, "Рассылаю…")
    async with session() as s:
        users = await services.segment_users(s, code, arg)
        n = await send_to_users(cq.bot, s, users, text, f"manual:{code}:{arg or ''}")
    await edit(
        cq,
        f"✉️ Рассылка отправлена.\n\nСегмент: <b>{texts.e(label)}</b>\nДоставлено: <b>{n}</b> из {len(users)}.",
        kb.back_kb("adm", "🛠 Панель"),
    )


# ---------- channels and settings ----------

@router.callback_query(F.data == "adm:cfg")
async def cb_cfg(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session() as s:
        reg_ch = await services.get_channel_id(s, "reg_channel_id")
        res_ch = await services.get_channel_id(s, "results_channel_id")
        flags = {k: await services.get_flag(s, k) for k in ("registration_open", "submissions_open", "moderation_required")}
    lines = ["⚙️ <b>Каналы и настройки</b>", ""]
    lines.append("<b>Канал заявок</b> — сюда падают анкеты новых участников с кнопками «Принять/Отклонить».")
    lines.append(f"{'✅ ' + f'<code>{reg_ch}</code>' if reg_ch else '⚠️ не подключён'}")
    lines.append("")
    lines.append("<b>Канал результатов</b> — сюда падают отчёты с файлами и кнопками «Зачесть/Отклонить». Баллы начисляются только после «Зачесть».")
    lines.append(f"{'✅ ' + f'<code>{res_ch}</code>' if res_ch else '⚠️ не подключён'}")
    lines.append("")
    lines.append("Переключатели: приём заявок, приём отчётов, обязательная модерация новых участников.")
    await edit(cq, "\n".join(lines), kb.config_kb(reg_ch, res_ch, flags))
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^adm:bind:(\w+)$"))
async def cb_bind(cq: CallbackQuery, state: FSMContext) -> None:
    key = cq.data.split(":")[2]
    name = "заявок" if key == "reg_channel_id" else "результатов"
    await state.set_state(AdminFlow.bind_channel)
    await state.update_data({ANCHOR_KEY: cq.message.message_id, "bind_key": key})
    await edit(
        cq,
        f"🔗 <b>Подключение канала {name}</b>\n\n"
        "1) Создай канал (можно приватный).\n"
        "2) Добавь этого бота в администраторы канала с правом «Публикация сообщений» и «Изменение сообщений».\n"
        "3) <b>Перешли сюда любой пост из этого канала</b> — бот запомнит его ID.\n\n"
        "Можно также прислать ID канала числом (например <code>-1001234567890</code>).",
        kb.bind_channel_kb(key),
    )
    await answer_cq(cq)


@router.message(AdminFlow.bind_channel)
async def bind_channel_input(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    key = data.get("bind_key")
    chat_id = None
    if message.forward_from_chat is not None:
        chat_id = message.forward_from_chat.id
    elif message.text and message.text.strip().lstrip("-").isdigit():
        chat_id = int(message.text.strip())
    await delete_quietly(message)
    if chat_id is None:
        await edit_anchor(
            message.bot, message.chat.id, state,
            "⚠️ Это не пересланный пост из канала и не ID. Перешли любой пост из нужного канала или пришли его ID числом.",
            kb.bind_channel_kb(key),
        )
        return
    try:
        probe = await message.bot.send_message(chat_id, "🔗 Канал подключён к боту марафона.")
        await message.bot.delete_message(chat_id, probe.message_id)
    except Exception as ex:  # noqa: BLE001
        await edit_anchor(
            message.bot, message.chat.id, state,
            f"❌ Не удалось написать в канал <code>{chat_id}</code>.\nДобавь бота администратором канала с правом публикации и повтори.\n\n<i>{texts.e(str(ex))}</i>",
            kb.bind_channel_kb(key),
        )
        return
    async with session() as s:
        await services.set_setting(s, key, str(chat_id))
        await s.commit()
    await state.clear()
    await edit_anchor(message.bot, message.chat.id, state, f"✅ Канал <code>{chat_id}</code> подключён.", kb.back_kb("adm:cfg", "⚙️ Настройки"))


@router.callback_query(F.data.regexp(r"^adm:unbind:(\w+)$"))
async def cb_unbind(cq: CallbackQuery, state: FSMContext) -> None:
    key = cq.data.split(":")[2]
    async with session() as s:
        await services.set_setting(s, key, "")
        await s.commit()
    await state.clear()
    await answer_cq(cq, "Канал отвязан")
    await cb_cfg(cq, state)


@router.callback_query(F.data == "adm:test_ch")
async def cb_test_channels(cq: CallbackQuery) -> None:
    await answer_cq(cq, "Проверяю…")
    results = []
    async with session() as s:
        for key, name in (("reg_channel_id", "Канал заявок"), ("results_channel_id", "Канал результатов")):
            chat_id = await services.get_channel_id(s, key)
            if not chat_id:
                results.append(f"⚪ {name}: не подключён")
                continue
            try:
                m = await cq.bot.send_message(chat_id, f"🧪 Тест связи: {name} работает.")
                await cq.bot.delete_message(chat_id, m.message_id)
                results.append(f"✅ {name}: отправка и удаление работают")
            except Exception as ex:  # noqa: BLE001
                results.append(f"❌ {name}: {texts.e(str(ex))[:120]}")
    await edit(cq, "🧪 <b>Проверка каналов</b>\n\n" + "\n".join(results), kb.back_kb("adm:cfg", "⚙️ Настройки"))


@router.callback_query(F.data.regexp(r"^adm:flag:(\w+)$"))
async def cb_flag(cq: CallbackQuery, state: FSMContext) -> None:
    key = cq.data.split(":")[2]
    async with session() as s:
        current = await services.get_flag(s, key)
        await services.set_setting(s, key, "0" if current else "1")
        await s.commit()
    await answer_cq(cq, "Выключено" if current else "Включено")
    await cb_cfg(cq, state)


# ---------- applications queue ----------

@router.callback_query(F.data.regexp(r"^adm:apps(?::(\d+))?$"))
async def cb_apps(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    parts = cq.data.split(":")
    page = int(parts[2]) if len(parts) > 2 else 0
    async with session() as s:
        users = await services.pending_users(s)
        reg_ch = await services.get_channel_id(s, "reg_channel_id")
    if not users:
        await edit(cq, "🙋 Заявок на модерации нет — все обработаны ✅", kb.back_kb("adm", "🛠 Панель"))
        await answer_cq(cq)
        return
    head = f"🙋 <b>Заявки на модерации: {len(users)}</b>\n\n"
    head += "Открой заявку, чтобы принять или отклонить." + ("" if reg_ch else "\n⚠️ Канал заявок не подключён — карточки приходят в личку.")
    await edit(cq, head, kb.applications_kb(users, page))
    await answer_cq(cq)


# ---------- tasks ----------

@router.callback_query(F.data == "adm:weeks")
async def cb_weeks_admin(cq: CallbackQuery, state: FSMContext) -> None:
    """Главный выключатель марафона: какие недели видят участники."""
    await state.clear()
    async with session() as s:
        tasks = await services.list_all_tasks(s)
    open_numbers = services.open_weeks()
    lines = ["📅 <b>Недели и задания</b>", "<i>что сейчас видят участники</i>", ""]
    for w in settings.weeks:
        active = len([t for t in tasks if t.week == w.number and t.is_active])
        state_txt = "🟢 открыта" if w.number in open_numbers else "🔴 закрыта"
        lines.append(f"<b>Неделя {w.number}</b> — {state_txt}")
        lines.append(f"заданий включено: {active} из {len([t for t in tasks if t.week == w.number])}")
        lines.append("")
    if not open_numbers:
        lines.append("Сейчас участники видят экран «Задания скоро откроются».")
    else:
        lines.append("Участникам видны задания открытых недель. Отчёты принимаются только по ним.")
    lines.append("")
    lines.append("Нажми на неделю, чтобы открыть или закрыть её.")
    await edit(cq, "\n".join(lines), kb.weeks_admin_kb(open_numbers))
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^adm:week_toggle:(\d+)$"))
async def cb_week_toggle(cq: CallbackQuery, state: FSMContext) -> None:
    week = int(cq.data.split(":")[2])
    now_open = not services.week_is_open(week)
    async with session() as s:
        await services.set_week_open(s, week, now_open)
        await s.commit()
    await answer_cq(cq, f"Неделя {week} {'открыта' if now_open else 'закрыта'}")
    await cb_weeks_admin(cq, state)


@router.callback_query(F.data == "adm:tasks")
async def cb_tasks_admin(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    async with session() as s:
        tasks = await services.list_all_tasks(s)
    await edit(
        cq,
        "📋 <b>Задания по одному</b>\n<i>тонкая настройка внутри недели</i>\n\n"
        "Нажми на задание, чтобы включить или выключить его показ участникам.\n"
        "🟢 активно · 🔴 скрыто. Задание видно, только если открыта его неделя.\n"
        "Тексты и баллы редактируются в файле <code>data/tasks.json</code>.",
        kb.tasks_admin_kb(tasks),
    )
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^adm:task_toggle:(\d+)$"))
async def cb_task_toggle(cq: CallbackQuery, state: FSMContext) -> None:
    task_id = int(cq.data.split(":")[2])
    async with session() as s:
        task = await services.get_task(s, task_id)
        if task is None:
            await answer_cq(cq, "Задание не найдено", alert=True)
            return
        task.is_active = not task.is_active
        await s.commit()
        state_txt = "включено" if task.is_active else "выключено"
    await answer_cq(cq, f"Задание №{task.code} {state_txt}")
    await cb_tasks_admin(cq, state)


# ---------- team management ----------

@router.callback_query(F.data.regexp(r"^adm:team:(\d+)$"))
async def cb_adm_team(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    team_id = int(cq.data.split(":")[2])
    async with session() as s:
        team = await services.get_team(s, team_id)
        if team is None:
            await answer_cq(cq, "Команда не найдена", alert=True)
            return
        rows = await services.leaderboard(s)
        rank, points = None, 0
        for i, r in enumerate(rows, 1):
            if r["team"].id == team_id:
                rank, points = i, r["points"]
        upts = await services.user_points_map(s)
        members = services.team_active_members(team)
        text = texts.team_card(team, members, points, rank, upts, team)
    await edit(cq, text, kb.admin_team_manage_kb(team))
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^adm:team_rename:(\d+)$"))
async def cb_team_rename(cq: CallbackQuery, state: FSMContext) -> None:
    team_id = int(cq.data.split(":")[2])
    await state.set_state(AdminFlow.team_rename)
    await state.update_data({ANCHOR_KEY: cq.message.message_id, "team_id": team_id})
    await edit(cq, "✏️ Напиши новое название команды сообщением:", kb.cancel_kb(f"adm:team:{team_id}"))
    await answer_cq(cq)


@router.message(AdminFlow.team_rename, F.text)
async def team_rename_text(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    team_id = data["team_id"]
    name = " ".join(message.text.split())[:80]
    await delete_quietly(message)
    async with session() as s:
        team = await services.get_team(s, team_id)
        names = [t.name for t in await services.list_teams(s) if t.id != team_id]
        if any(n.casefold() == name.casefold() for n in names):
            await edit_anchor(message.bot, message.chat.id, state, "⚠️ Такое название уже занято. Напиши другое:", kb.cancel_kb(f"adm:team:{team_id}"))
            return
        old = team.name
        team.name = name
        await s.commit()
        team = await services.get_team(s, team_id)
        members = [m.tg_id for m in team.members]
    for tg_id in members:
        try:
            await message.bot.send_message(tg_id, texts.push_team_renamed(old, team))
        except Exception:  # noqa: BLE001
            pass
    await state.clear()
    await edit_anchor(message.bot, message.chat.id, state, f"✅ Команда переименована в <b>{texts.e(name)}</b>.", kb.back_kb("adm:teams", "🏷 Команды"))


@router.callback_query(F.data.regexp(r"^adm:team_del:(\d+)$"))
async def cb_team_del(cq: CallbackQuery) -> None:
    team_id = int(cq.data.split(":")[2])
    async with session() as s:
        team = await services.get_team(s, team_id)
        n = len(team.members) if team else 0
    if team is None:
        await answer_cq(cq, "Команда не найдена", alert=True)
        return
    await edit(
        cq,
        f"🗑 Удалить команду <b>{texts.e(team.name)}</b>?\n\nУчастников в ней: {n} — они останутся без команды и смогут выбрать другую. "
        "Их зачтённые баллы сохранятся.",
        kb.admin_team_del_kb(team),
    )
    await answer_cq(cq)


@router.callback_query(F.data.regexp(r"^adm:team_del_ok:(\d+)$"))
async def cb_team_del_ok(cq: CallbackQuery, state: FSMContext) -> None:
    team_id = int(cq.data.split(":")[2])
    async with session() as s:
        team = await services.get_team(s, team_id)
        if team is None:
            await answer_cq(cq, "Команда не найдена", alert=True)
            return
        members = list(team.members)
        for m in members:
            m.team_id = None
        await s.flush()
        await s.delete(team)
        await s.commit()
    for m in members:
        try:
            await cq.bot.send_message(m.tg_id, texts.push_team_disbanded())
        except Exception:  # noqa: BLE001
            pass
    await answer_cq(cq, "Команда удалена")
    await cb_adm_teams(cq)


# ---------- reminders ----------

@router.callback_query(F.data == "adm:remind")
async def cb_remind(cq: CallbackQuery) -> None:
    cw = services.current_week()
    if not cw:
        await answer_cq(cq, "Сейчас нет активной недели", alert=True)
        return
    await answer_cq(cq, "Отправляю напоминания…")
    async with session() as s:
        users = await services.segment_users(s, "no_reports_week")
        n = await send_to_users(cq.bot, s, users, texts.week_reminder(cw.number), f"reminder:{cw.number}:manual")
    await edit(cq, f"⏰ Напоминание отправлено {n} участникам без отчётов на {cw.number} неделе.", kb.back_kb("adm", "🛠 Панель"))


# ---------- stats / export ----------

@router.callback_query(F.data == "adm:stats")
async def cb_stats(cq: CallbackQuery) -> None:
    async with session() as s:
        users = await services.list_participants(s)
        rows = await services.leaderboard(s)
        subs_all = []
        for u in users:
            subs_all.extend(await services.user_submissions(s, u.id))
        cw = services.current_week()
        idle = await services.users_without_submissions(s, cw.number) if cw else []
    reg = [u for u in users if u.status.value == "registered"]
    lines = ["📈 <b>Статистика</b>", ""]
    lines.append(f"Участников: {len(reg)} (без команды: {len([u for u in reg if not u.team_id])}, дисквалифицировано: {len([u for u in users if u.status.value == 'disqualified'])})")
    lines.append(f"Команд: {len(rows)} (неполных: {len([r for r in rows if len(r['members']) < settings.team_size])})")
    for w in settings.weeks:
        ws = [x for x in subs_all if x.week == w.number]
        lines.append(
            f"Неделя {w.number}: отправлено {len([x for x in ws if x.status in (SubmissionStatus.pending, SubmissionStatus.approved, SubmissionStatus.rejected)])}, "
            f"зачтено {len([x for x in ws if x.status == SubmissionStatus.approved])}, отклонено {len([x for x in ws if x.status == SubmissionStatus.rejected])}, "
            f"на проверке {len([x for x in ws if x.status == SubmissionStatus.pending])}"
        )
    if cw:
        lines.append(f"\nБез отчётов на текущей неделе: {len(idle)}")
    await edit(cq, "\n".join(lines), kb.back_kb("adm", "🛠 Панель"))
    await answer_cq(cq)


@router.callback_query(F.data == "adm:export")
async def cb_export(cq: CallbackQuery) -> None:
    await answer_cq(cq, "Формирую файл…")
    path = Path("data") / "export.xlsx"
    async with session() as s:
        await export_xlsx(s, path)
    await cq.message.answer_document(FSInputFile(path, filename=f"marathon_{settings.today().isoformat()}.xlsx"), caption="📥 Выгрузка: участники, команды, отчёты, журнал баллов")
