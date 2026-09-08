from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import settings
from .models import Submission, SubmissionStatus, Task, Team, User


def _btn(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb)


def webapp_button(text: str = "📱 Открыть приложение") -> InlineKeyboardButton | None:
    if settings.webapp_url.startswith("https://"):
        return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=settings.webapp_url + "/"))
    return None


def start_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("📜 Правила", "rules"))
    kb.row(_btn("✅ Зарегистрироваться", "reg:start"))
    return kb.as_markup()


def rules_kb(registered: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if registered:
        kb.row(_btn("⬅️ В меню", "menu"))
    else:
        kb.row(_btn("✅ Зарегистрироваться", "reg:start"))
        kb.row(_btn("⬅️ Назад", "start"))
    return kb.as_markup()


def reg_kb(step: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if step in ("department", "city"):
        kb.row(_btn("⏭ Пропустить", f"reg:skip:{step}"))
    if step == "confirm":
        kb.row(_btn("✅ Подтвердить и принять правила", "reg:confirm"))
        kb.row(_btn("✏️ Заполнить заново", "reg:start"))
    kb.row(_btn("❌ Отмена", "start"))
    return kb.as_markup()


def main_menu_kb(user: User, pending: int = 0) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("📋 Задания", "tasks"), _btn("👥 Команды", "teams"))
    kb.row(_btn("🏆 Рейтинг", "top"), _btn("📊 Мои результаты", "me"))
    wa = webapp_button()
    if wa:
        kb.row(wa)
    kb.row(_btn("📜 Правила", "rules"), _btn("🆘 Помощь P&C", "help"))
    if user.is_admin:
        kb.row(_btn("🛠 Панель P&C" + (f" · {pending} на проверке" if pending else ""), "adm"))
    return kb.as_markup()


def back_kb(cb: str = "menu", text: str = "⬅️ В меню") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(text, cb)]])


def help_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("👥 Попросить распределить меня в команду", "help:team"))
    kb.row(_btn("❓ Другой вопрос", "help:other"))
    kb.row(_btn("⬅️ В меню", "menu"))
    return kb.as_markup()


# ---------- teams ----------

def teams_kb(rows: list[dict], user: User) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for r in rows:
        t: Team = r["team"]
        n = len(r["members"])
        full = n >= settings.team_size
        mark = "✔️ " if user.team_id == t.id else ("🔒 " if full else "")
        kb.row(_btn(f"{mark}{t.emoji} {t.name} ({n}/{settings.team_size})", f"team:{t.id}"))
    if not user.team_id:
        kb.row(_btn("➕ Создать команду", "team:new"))
        kb.row(_btn("🆘 Помощь P&C с распределением", "help:team"))
    kb.row(_btn("⬅️ В меню", "menu"))
    return kb.as_markup()


def team_card_kb(team: Team, user: User, can_join: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if user.team_id == team.id:
        if settings.marathon_status() == "before":
            kb.row(_btn("🚪 Покинуть команду", f"team:leave:{team.id}"))
    elif can_join and not user.team_id:
        kb.row(_btn("✅ Вступить в команду", f"team:join:{team.id}"))
    kb.row(_btn("⬅️ К списку команд", "teams"))
    return kb.as_markup()


def team_confirm_join_kb(team_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("✅ Да, вступить", f"team:join_ok:{team_id}"), _btn("⬅️ Назад", f"team:{team_id}"))
    return kb.as_markup()


def team_emoji_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    emojis = ["🌱", "🌟", "🔥", "🚀", "🦊", "🐝", "🌈", "💪", "🍀", "🦁", "🐬", "🎯"]
    for i in range(0, len(emojis), 6):
        kb.row(*[_btn(x, f"team:emoji:{x}") for x in emojis[i : i + 6]])
    kb.row(_btn("❌ Отмена", "teams"))
    return kb.as_markup()


def cancel_kb(cb: str = "teams") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("❌ Отмена", cb)]])


# ---------- tasks ----------

def week_tabs_kb(active: int, tasks: list[Task], subs: dict[int, Submission]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(*[_btn(("• " if w.number == active else "") + f"{w.number} нед.", f"tasks:w:{w.number}") for w in settings.weeks])
    for t in tasks:
        sub = subs.get(t.id)
        icon = ""
        if sub and sub.status != SubmissionStatus.cancelled:
            icon = {"draft": "📝", "pending": "⏳", "approved": "✅", "rejected": "❌"}[sub.status.value] + " "
        kb.row(_btn(f"{icon}{t.emoji} №{t.code} {t.title[:34]} · {t.points_label}", f"task:{t.id}"))
    kb.row(_btn("⬅️ В меню", "menu"))
    return kb.as_markup()


def task_card_kb(task: Task, sub: Submission | None, is_open: bool, user: User) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    active = user.status.value == "registered"
    can_start = is_open and active and (sub is None or sub.status in (SubmissionStatus.rejected, SubmissionStatus.cancelled))
    if sub and sub.status == SubmissionStatus.draft and is_open:
        kb.row(_btn("📤 Продолжить отчёт", f"sub:open:{sub.id}"))
    elif can_start:
        if task.options:
            for o in task.options:
                kb.row(_btn(f"▸ {o.title} — {o.points} б.", f"sub:start:{task.id}:{o.id}"))
        else:
            kb.row(_btn("📤 Выполнить и отправить отчёт", f"sub:start:{task.id}:0"))
    if sub and sub.status == SubmissionStatus.pending:
        kb.row(_btn("🚫 Отозвать отчёт", f"sub:cancel:{sub.id}"))
    kb.row(_btn("⬅️ К заданиям", f"tasks:w:{task.week}"))
    return kb.as_markup()


def submission_editor_kb(sub: Submission) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("✅ Отправить на проверку", f"sub:send:{sub.id}"))
    if sub.files:
        kb.row(_btn("🗑 Удалить последний файл", f"sub:pop:{sub.id}"), _btn("👁 Показать файлы", f"sub:preview:{sub.id}"))
    kb.row(_btn("❌ Отменить отчёт", f"sub:cancel:{sub.id}"), _btn("⬅️ К заданию", f"task:{sub.task_id}"))
    return kb.as_markup()


def sub_cancel_confirm_kb(sub: Submission) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("🚫 Да, отменить", f"sub:cancel_ok:{sub.id}"), _btn("⬅️ Назад", f"task:{sub.task_id}"))
    return kb.as_markup()


# ---------- admin ----------

def admin_menu_kb(pending: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn(f"🔎 Очередь проверки ({pending})", "adm:queue"))
    kb.row(_btn("👥 Участники", "adm:users:0"), _btn("🏷 Команды", "adm:teams"))
    kb.row(_btn("📣 Анонс недели", "adm:announce"), _btn("✉️ Рассылка", "adm:bcast"))
    kb.row(_btn("📈 Статистика", "adm:stats"), _btn("📥 Экспорт Excel", "adm:export"))
    kb.row(_btn("⬅️ В меню", "menu"))
    return kb.as_markup()


def review_kb(sub: Submission, idx: int, total: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("👁 Показать файлы", f"adm:files:{sub.id}"))
    kb.row(_btn(f"✅ Зачесть +{sub.target_points}", f"adm:ok:{sub.id}"), _btn("❌ Отклонить", f"adm:rej:{sub.id}"))
    nav = []
    if idx > 1:
        nav.append(_btn("⬅️", f"adm:queue:{idx - 2}"))
    if idx < total:
        nav.append(_btn("➡️", f"adm:queue:{idx}"))
    if nav:
        kb.row(*nav)
    kb.row(_btn("👤 Участник", f"adm:user:{sub.user_id}"), _btn("🛠 Панель", "adm"))
    return kb.as_markup()


def reject_reason_kb(sub_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    reasons = [
        ("Нет вас в кадре", "r1"),
        ("Не видно передачи / процесса", "r2"),
        ("Не хватает фото по условиям", "r3"),
        ("Нет описания / заметки", "r4"),
        ("Выполнено до публикации задания", "r5"),
    ]
    for text, code in reasons:
        kb.row(_btn(text, f"adm:rej_r:{sub_id}:{code}"))
    kb.row(_btn("✍️ Своя причина", f"adm:rej_custom:{sub_id}"))
    kb.row(_btn("⬅️ Назад", f"adm:sub:{sub_id}"))
    return kb.as_markup()


REJECT_REASONS = {
    "r1": "На фото нет вас в кадре.",
    "r2": "На фото не видно передачи / процесса выполнения.",
    "r3": "Не хватает фото по условиям зачёта.",
    "r4": "Нет описания / заметки по условиям зачёта.",
    "r5": "Идентичная деятельность выполнена до публикации задания.",
}


def users_list_kb(users: list[User], page: int, per_page: int = 10) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    chunk = users[page * per_page : (page + 1) * per_page]
    for u in chunk:
        st = {"registered": "", "disqualified": "🚫 ", "new": "🆕 "}[u.status.value]
        team = f" · {u.team.emoji}" if u.team else " · ❔"
        kb.row(_btn(f"{st}{u.display_name[:30]}{team}", f"adm:user:{u.id}"))
    nav = []
    if page > 0:
        nav.append(_btn("⬅️", f"adm:users:{page - 1}"))
    if (page + 1) * per_page < len(users):
        nav.append(_btn("➡️", f"adm:users:{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.row(_btn("🛠 Панель", "adm"))
    return kb.as_markup()


def admin_user_kb(u: User) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if u.status.value == "disqualified":
        kb.row(_btn("♻️ Восстановить", f"adm:reinstate:{u.id}"))
    elif u.status.value == "registered":
        kb.row(_btn("🚫 Дисквалифицировать", f"adm:dq:{u.id}"))
    kb.row(_btn("🔀 Перевести в команду", f"adm:move:{u.id}"))
    kb.row(_btn("⬅️ Участники", "adm:users:0"), _btn("🛠 Панель", "adm"))
    return kb.as_markup()


def dq_confirm_kb(u: User) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("🚫 Подтвердить дисквалификацию", f"adm:dq_ok:{u.id}"))
    kb.row(_btn("⬅️ Назад", f"adm:user:{u.id}"))
    return kb.as_markup()


def move_team_kb(u: User, teams: list[Team]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for t in teams:
        n = len([m for m in t.members if m.status.value != "disqualified"])
        mark = "✔️ " if u.team_id == t.id else ""
        kb.row(_btn(f"{mark}{t.emoji} {t.name} ({n}/{settings.team_size})", f"adm:move_to:{u.id}:{t.id}"))
    kb.row(_btn("➖ Убрать из команды", f"adm:move_to:{u.id}:0"))
    kb.row(_btn("⬅️ Назад", f"adm:user:{u.id}"))
    return kb.as_markup()


def admin_teams_kb(rows: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for r in rows:
        t = r["team"]
        kb.row(_btn(f"{t.emoji} {t.name} ({len(r['members'])}/{settings.team_size}) · {r['points']} б.", f"team:{t.id}"))
    kb.row(_btn("🛠 Панель", "adm"))
    return kb.as_markup()


def announce_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(*[_btn(f"📣 Неделя {w.number}", f"adm:announce_ok:{w.number}") for w in settings.weeks])
    kb.row(_btn("⬅️ Панель", "adm"))
    return kb.as_markup()


def bcast_confirm_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("✉️ Отправить всем", "adm:bcast_ok"), _btn("❌ Отмена", "adm"))
    return kb.as_markup()
