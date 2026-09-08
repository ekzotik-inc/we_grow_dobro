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
from ...models import Broadcast, SubmissionStatus
from ..common import ANCHOR_KEY, answer_cq, delete_quietly, edit, edit_anchor, load_user, session
from ..states import AdminFlow
from .tasks import send_files

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


async def render_admin(s):
    pending = await services.pending_count(s)
    return (
        f"🛠 <b>Панель P&C</b>\n\nОтчётов на проверке: <b>{pending}</b>\n"
        f"Текущая неделя: {settings.current_week().number if settings.current_week() else '— (' + settings.marathon_status() + ')'}",
        kb.admin_menu_kb(pending),
    )


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
            f"✅ <b>Задание №{sub.task.code} «{texts.e(sub.task.title)}» зачтено!</b>\n"
            f"Начислено <b>+{sub.points_awarded}</b> баллов в командный зачёт 🎉"
            + (f"\n\nКомментарий P&C: <i>{texts.e(sub.review_comment)}</i>" if sub.review_comment else "")
        )
    else:
        text = (
            f"❌ <b>Задание №{sub.task.code} «{texts.e(sub.task.title)}» не зачтено.</b>\n"
            f"Причина: <i>{texts.e(sub.review_comment or 'условия зачёта не выполнены')}</i>\n\n"
            "Ты можешь исправить и отправить отчёт заново, пока неделя задания активна."
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
        queue = await services.pending_submissions(s)
        if queue:
            await _render_sub(cq, s, queue[0], 1, len(queue))
        else:
            await edit(cq, f"✅ Отчёт #{sub.id} зачтён (+{sub.points_awarded}).\n\nОчередь пуста.", kb.back_kb("adm", "🛠 Панель"))
    await answer_cq(cq, f"Зачтено +{sub.points_awarded}")


@router.callback_query(F.data.regexp(r"^adm:rej:(\d+)$"))
async def cb_reject(cq: CallbackQuery) -> None:
    sub_id = int(cq.data.split(":")[2])
    await edit(cq, f"❌ <b>Отклонить отчёт #{sub_id}</b>\n\nВыбери причину — она будет отправлена участнику:", kb.reject_reason_kb(sub_id))
    await answer_cq(cq)


async def _do_reject(cq_or_msg, state: FSMContext, sub_id: int, reason: str, actor_id: int) -> None:
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
    await _do_reject(cq, state, int(sub_id), kb.REJECT_REASONS.get(code, "Условия зачёта не выполнены."), cq.from_user.id)
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


# ---------- participants ----------

@router.callback_query(F.data.regexp(r"^adm:users:(\d+)$"))
async def cb_users(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    page = int(cq.data.split(":")[2])
    async with session() as s:
        users = await services.list_participants(s)
    no_team = len([u for u in users if not u.team_id and u.status.value == "registered"])
    await edit(cq, f"👥 <b>Участники</b> — {len(users)} чел.\nБез команды: {no_team}\n\n🆕 не завершил регистрацию · 🚫 дисквалифицирован · ❔ без команды", kb.users_list_kb(users, page))
    await answer_cq(cq)


async def _user_card(s, u) -> str:
    pts = await services.user_points(s, u.id)
    subs = await services.user_submissions(s, u.id)
    lines = [f"👤 <b>{texts.e(u.display_name)}</b>" + (f" (@{texts.e(u.username)})" if u.username else ""), f"tg id: <code>{u.tg_id}</code>"]
    if u.department:
        lines.append(f"🏢 {texts.e(u.department)}")
    if u.city:
        lines.append(f"📍 {texts.e(u.city)}")
    lines.append(f"👥 Команда: {texts.e(u.team.emoji + ' ' + u.team.name) if u.team else '— (без команды)'}")
    lines.append(f"⭐ Баллы: {pts} · статус: {u.status.value}")
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
        await message.bot.send_message(u.tg_id, f"🚫 <b>Вы дисквалифицированы с марафона.</b>\nПричина: {texts.e(reason)}\nВаши результаты не учитываются в командном зачёте. Вопросы — к сотруднику P&C.")
    except Exception:  # noqa: BLE001
        pass
    await state.clear()
    await edit_anchor(message.bot, message.chat.id, state, "🚫 Участник дисквалифицирован.\n\n" + text, kb.admin_user_kb(u))


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
        await cq.bot.send_message(u.tg_id, "♻️ Ваше участие в марафоне восстановлено. Баллы снова учитываются в командном зачёте.")
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
            await cq.bot.send_message(u.tg_id, f"👥 Сотрудник P&C распределил вас в команду <b>{texts.e(u.team.emoji)} {texts.e(u.team.name)}</b>.", reply_markup=kb.back_kb("menu", "🏠 Меню"))
        else:
            await cq.bot.send_message(u.tg_id, "👥 Сотрудник P&C убрал вас из команды. Выберите новую команду в меню.", reply_markup=kb.back_kb("teams", "👥 Команды"))
    except Exception:  # noqa: BLE001
        pass
    await edit(cq, "✅ Готово.\n\n" + text, kb.admin_user_kb(u))
    await answer_cq(cq)


@router.callback_query(F.data == "adm:teams")
async def cb_adm_teams(cq: CallbackQuery) -> None:
    async with session() as s:
        rows = await services.leaderboard(s)
    await edit(cq, texts.leaderboard_text(rows), kb.admin_teams_kb(rows))
    await answer_cq(cq)


# ---------- announcements / broadcast ----------

async def broadcast(bot, s, text: str, kind: str, user_filter=None) -> int:
    users = await services.list_participants(s)
    n = 0
    for u in users:
        if u.status.value != "registered":
            continue
        if user_filter and not user_filter(u):
            continue
        try:
            await bot.send_message(u.tg_id, text, reply_markup=kb.back_kb("menu", "🏠 Меню"))
            n += 1
        except Exception as ex:  # noqa: BLE001
            log.warning("broadcast to %s failed: %s", u.tg_id, ex)
        await asyncio.sleep(0.05)  # ~20 msg/s, below Telegram's limit
    s.add(Broadcast(kind=kind, recipients=n))
    await s.commit()
    return n


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
        n = await broadcast(cq.bot, s, texts.week_announce(week, tasks), f"week_announce:{week}:manual")
    await edit(cq, f"📣 Анонс недели {week} отправлен {n} участникам.", kb.back_kb("adm", "🛠 Панель"))


@router.callback_query(F.data == "adm:bcast")
async def cb_bcast(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFlow.broadcast)
    await state.update_data({ANCHOR_KEY: cq.message.message_id})
    await edit(cq, "✉️ Напиши текст рассылки сообщением (поддерживается HTML-разметка Telegram). Получат все активные участники.", kb.cancel_kb("adm"))
    await answer_cq(cq)


@router.message(AdminFlow.broadcast, F.text)
async def bcast_text(message: Message, state: FSMContext) -> None:
    await state.update_data(bcast_text=message.html_text)
    await delete_quietly(message)
    await edit_anchor(message.bot, message.chat.id, state, "Предпросмотр:\n\n" + message.html_text + "\n\nОтправить?", kb.bcast_confirm_kb())


@router.callback_query(AdminFlow.broadcast, F.data == "adm:bcast_ok")
async def cb_bcast_ok(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    text = data.get("bcast_text", "")
    await state.clear()
    await answer_cq(cq, "Рассылаю…")
    async with session() as s:
        n = await broadcast(cq.bot, s, text, "manual")
    await edit(cq, f"✉️ Рассылка отправлена {n} участникам.", kb.back_kb("adm", "🛠 Панель"))


# ---------- stats / export ----------

@router.callback_query(F.data == "adm:stats")
async def cb_stats(cq: CallbackQuery) -> None:
    async with session() as s:
        users = await services.list_participants(s)
        rows = await services.leaderboard(s)
        subs_all = []
        for u in users:
            subs_all.extend(await services.user_submissions(s, u.id))
        cw = settings.current_week()
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
