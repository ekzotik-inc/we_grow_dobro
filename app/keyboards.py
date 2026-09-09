from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from . import emoji
from .config import settings
from .models import Submission, SubmissionStatus, Task, Team, User


# Colour hints (Bot API 9.4): the leading character already tells the user what a button does,
# so the style is derived from it instead of being repeated at every call site.
_STYLE_BY_CHAR = {
    "✅": "success",
    "❌": "danger",
    "🚫": "danger",
    "🗑": "danger",
}


def _btn(text: str, cb: str, style: str | None = None) -> InlineKeyboardButton:
    icon, label = emoji.button_icon(text)
    return InlineKeyboardButton(
        text=label,
        callback_data=cb,
        icon_custom_emoji_id=icon,
        style=style or _STYLE_BY_CHAR.get(text[:1]),
    )


def start_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("🌱 Участвовать", "reg:start"))
    kb.row(_btn("📖 Правила", "rules"))
    return kb.as_markup()


def rules_kb(registered: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if registered:
        kb.row(_btn("⬅️ В меню", "menu"))
    else:
        kb.row(_btn("🌱 Участвовать", "reg:start"))
        kb.row(_btn("⬅️ Назад", "start"))
    return kb.as_markup()


def reg_kb(step: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if step == "confirm":
        kb.row(_btn("✅ Отправить заявку", "reg:confirm"))
        kb.row(_btn("✏️ Заполнить заново", "reg:start"))
    kb.row(_btn("❌ Отмена", "start"))
    return kb.as_markup()


def phone_request_kb() -> ReplyKeyboardMarkup:
    """Telegram can only share a phone number through a reply keyboard button."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Поделиться номером", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="или напиши номер вручную",
    )


def reg_team_kb(rows: list[dict]) -> InlineKeyboardMarkup:
    """Team choice during sign-up. P&C makes the final call, so full teams are shown too."""
    kb = InlineKeyboardBuilder()
    for r in rows:
        team = r["team"]
        n = len(r["members"])
        full = " · мест нет" if n >= settings.team_size else ""
        kb.row(_btn(f"{team.emoji} {team.name} ({n}/{settings.team_size}){full}", f"reg:team:{team.id}"))
    kb.row(_btn("🤝 Пусть P&C подберёт команду", "reg:team:0"))
    kb.row(_btn("❌ Отмена", "start"))
    return kb.as_markup()


def main_menu_kb(user: User, pending: int = 0) -> InlineKeyboardMarkup:
    """Main menu: the action to take right now spans the full width, everything else pairs up below."""
    kb = InlineKeyboardBuilder()
    in_team = bool(user.team_id)
    from .services import open_weeks

    started = bool(open_weeks())

    # The primary button is whatever the participant should do next.
    if not in_team:
        # A participant never picks a team — P&C assigns it, so the menu offers the tasks and the
        # way to reach P&C instead of a picker that would do nothing.
        kb.row(_btn("📋 Задания недели", "tasks", style="primary"))
        kb.row(_btn("🌱 Команды марафона", "teams"))
    elif started:
        kb.row(_btn("📋 Задания недели", "tasks", style="primary"))
        kb.row(_btn("🌱 Моя команда", f"team:{user.team_id}"))
    else:
        kb.row(_btn("🌱 Моя команда", f"team:{user.team_id}", style="primary"))
        kb.row(_btn("📋 Задания недели", "tasks"))

    kb.row(_btn("⚡ Мой вклад", "me"), _btn("🏆 Рейтинг", "top"))
    kb.row(_btn("📖 Правила", "rules"), _btn("💬 Помощь", "help"))
    # Кнопка панели — по тому же правилу, что и доступ к ней: только ADMIN_IDS / PC_IDS.
    if settings.is_admin(user.tg_id):
        kb.row(_btn("🛠 Панель P&C" + (f" · {pending}" if pending else ""), "adm"))
    return kb.as_markup()


def pending_kb(user: User) -> InlineKeyboardMarkup:
    """Menu for an applicant whose registration is not approved yet."""
    kb = InlineKeyboardBuilder()
    kb.row(_btn("🔄 Проверить, приняли ли заявку", "menu"))
    if user.status.value == "rejected":
        kb.row(_btn("📝 Заполнить анкету заново", "reg:start"))
    kb.row(_btn("📖 Правила", "rules"), _btn("💬 Помощь", "help"))
    return kb.as_markup()


def back_kb(cb: str = "menu", text: str = "⬅️ В меню") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn(text, cb)]])


def help_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("🌱 Помогите выбрать команду", "help:team"))
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
    kb.row(_btn("⬅️ В меню", "menu"))
    return kb.as_markup()


def team_card_kb(team: Team, user: User, can_join: bool) -> InlineKeyboardMarkup:
    """Read-only for participants: joining and leaving is a P&C decision."""
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ К списку команд", "teams"))
    return kb.as_markup()


def team_emoji_kb(cancel: str = "adm:teams") -> InlineKeyboardMarkup:
    """Symbol picker for a new team. Labels here are a single emoji — see emoji.button_icon."""
    kb = InlineKeyboardBuilder()
    emojis = ["🌱", "🌟", "🔥", "🚀", "🦊", "🐝", "🌈", "💪", "🍀", "🦁", "🐬", "🎯"]
    for i in range(0, len(emojis), 6):
        kb.row(*[_btn(x, f"team:emoji:{x}") for x in emojis[i : i + 6]])
    kb.row(_btn("❌ Отмена", cancel))
    return kb.as_markup()


def cancel_kb(cb: str = "teams") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("❌ Отмена", cb)]])


# ---------- tasks ----------

def week_tabs_kb(active: int, tasks: list[Task], subs: dict[int, Submission]) -> InlineKeyboardMarkup:
    from .services import week_is_open

    kb = InlineKeyboardBuilder()
    # A week that has not started yet is not shown at all — no peeking ahead.
    tabs = [
        _btn(("• " if w.number == active else "") + f"Неделя {w.number}", f"tasks:w:{w.number}")
        for w in settings.weeks
        if week_is_open(w.number)
    ]
    if tabs:
        kb.row(*tabs)
    # The list above already names every task, so the buttons stay short and predictable:
    # «Задание 1» … «Задание 4», with a status mark when there is something to report.
    for i, t in enumerate(tasks, 1):
        sub = subs.get(t.id)
        mark = ""
        if sub and sub.status != SubmissionStatus.cancelled:
            mark = " · " + {"draft": "📝", "pending": "⏳", "approved": "✅", "rejected": "❌"}[sub.status.value]
        kb.row(_btn(f"{t.emoji} Задание {i}{mark}", f"task:{t.id}"))
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
                kb.row(_btn(f"{o.title} — {o.points} б.", f"sub:start:{task.id}:{o.id}"))
        else:
            kb.row(_btn("📤 Сделать и отправить отчёт", f"sub:start:{task.id}:0"))
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


def submission_step_kb(sub: Submission, index: int, total: int, done: bool) -> InlineKeyboardMarkup:
    """Кнопки шага: назад по шагам, к проверке — и всегда выход из мастера."""
    kb = InlineKeyboardBuilder()
    if done:
        kb.row(_btn("➡️ Дальше", f"sub:step:{sub.id}:{index + 1}", style="primary"))
        kb.row(_btn("🔄 Переснять этот шаг", f"sub:redo:{sub.id}:{index}"))
    nav = []
    if index > 0:
        nav.append(_btn("⬅️ Прошлый шаг", f"sub:step:{sub.id}:{index - 1}"))
    if index < total - 1 and done:
        nav.append(_btn("➡️ Следующий", f"sub:step:{sub.id}:{index + 1}"))
    if nav:
        kb.row(*nav)
    kb.row(_btn("📋 Все шаги", f"sub:review:{sub.id}"))
    kb.row(_btn("❌ Отменить отчёт", f"sub:cancel:{sub.id}"), _btn("⬅️ К заданию", f"task:{sub.task_id}"))
    return kb.as_markup()


def submission_review_kb(sub: Submission, steps: list[dict], done: list[bool], ready: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if ready:
        kb.row(_btn("✅ Отправить на проверку", f"sub:send:{sub.id}"))
    for i, st in enumerate(steps):
        mark = "✅" if done[i] else "⏳"
        kb.row(_btn(f"{mark} Шаг {i + 1}. {st['title'][:28]}", f"sub:step:{sub.id}:{i}"))
    if sub.files:
        kb.row(_btn("👁 Показать файлы", f"sub:preview:{sub.id}"))
    kb.row(_btn("❌ Отменить отчёт", f"sub:cancel:{sub.id}"), _btn("⬅️ К заданию", f"task:{sub.task_id}"))
    return kb.as_markup()


def sub_cancel_confirm_kb(sub: Submission) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("🚫 Да, отменить", f"sub:cancel_ok:{sub.id}"), _btn("⬅️ Назад", f"task:{sub.task_id}"))
    return kb.as_markup()


# ---------- /addresult: ручная корректировка (только владелец) ----------

def results_root_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("👤 Выбрать участника", "res:users:0", style="primary"))
    kb.row(_btn("🏷 Выбрать команду", "res:teams"))
    kb.row(_btn("🧾 Последние начисления", "res:log"))
    kb.row(_btn("⬅️ В меню", "menu"))
    return kb.as_markup()


def results_users_kb(users: list[User], points: dict[int, int], page: int = 0, per: int = 8) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    chunk = users[page * per : page * per + per]
    for u in chunk:
        kb.row(_btn(f"{u.display_name[:28]} · {points.get(u.id, 0)} б.", f"res:user:{u.id}"))
    nav = []
    if page > 0:
        nav.append(_btn("⬅️", f"res:users:{page - 1}"))
    if (page + 1) * per < len(users):
        nav.append(_btn("➡️", f"res:users:{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.row(_btn("🔍 Найти участника", "res:find"))
    kb.row(_btn("⬅️ Назад", "res"))
    return kb.as_markup()


def results_user_kb(user: User, subs: list[Submission]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("➕ Начислить баллы", f"res:add:{user.id}", style="primary"),
           _btn("➖ Списать баллы", f"res:sub:{user.id}"))
    for x in subs:
        if x.status == SubmissionStatus.approved:
            kb.row(_btn(f"↩️ Отменить зачёт №{x.task.code} (+{x.points_awarded})", f"res:revoke:{x.id}"))
    kb.row(_btn("🗑 Обнулить результаты участника", f"res:wipe_user:{user.id}"))
    kb.row(_btn("⬅️ Участники", "res:users:0"), _btn("🛠 Раздел", "res"))
    return kb.as_markup()


def results_teams_kb(rows: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for r in rows:
        t: Team = r["team"]
        kb.row(_btn(f"{t.emoji} {t.name} · {r['points']} б.", f"res:team:{t.id}"))
    kb.row(_btn("⬅️ Назад", "res"))
    return kb.as_markup()


def results_team_kb(team: Team, members: list[User]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for m in members:
        kb.row(_btn(f"👤 {m.display_name[:30]}", f"res:user:{m.id}"))
    kb.row(_btn("🗑 Обнулить результаты команды", f"res:wipe_team:{team.id}"))
    kb.row(_btn("⬅️ Команды", "res:teams"), _btn("🛠 Раздел", "res"))
    return kb.as_markup()


def results_reason_kb(user_id: int) -> InlineKeyboardMarkup:
    """Причину можно не писать — иначе начисление зависало бы на этом шаге."""
    kb = InlineKeyboardBuilder()
    kb.row(_btn("✅ Начислить без причины", f"res:noreason:{user_id}", style="primary"))
    kb.row(_btn("⬅️ Отмена", f"res:user:{user_id}"))
    return kb.as_markup()


def results_confirm_kb(action: str, target_id: int, back: str) -> InlineKeyboardMarkup:
    """Подтверждение необратимого действия: обнуления результатов."""
    kb = InlineKeyboardBuilder()
    kb.row(_btn("🗑 Да, обнулить", f"res:{action}_ok:{target_id}"))
    kb.row(_btn("⬅️ Отмена", back))
    return kb.as_markup()


def results_back_kb(cb: str = "res") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("⬅️ Раздел корректировки", cb)]])


# ---------- admin ----------

def admin_menu_kb(pending: int, applications: int = 0) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn(f"🔎 Отчёты на проверке ({pending})", "adm:queue"), _btn(f"🙋 Заявки ({applications})", "adm:apps"))
    kb.row(_btn("👥 Участники", "adm:users:0"), _btn("🏷 Команды", "adm:teams"))
    kb.row(_btn("✉️ Рассылка по сегментам", "adm:bcast"))
    kb.row(_btn("📣 Анонс недели", "adm:announce"), _btn("⏰ Напомнить сейчас", "adm:remind"))
    kb.row(_btn("📅 Недели и задания", "adm:weeks"), _btn("📈 Статистика", "adm:stats"))
    kb.row(_btn("⚙️ Каналы и настройки", "adm:cfg"), _btn("📥 Экспорт Excel", "adm:export"))
    kb.row(_btn("⬅️ В меню", "menu"))
    return kb.as_markup()


# ---------- moderation of applications ----------

def moderation_kb(user_id: int) -> InlineKeyboardMarkup:
    """Buttons under an application card (works in the channel and in a DM)."""
    kb = InlineKeyboardBuilder()
    kb.row(_btn("✅ Принять", f"mod:ok:{user_id}"), _btn("❌ Отклонить", f"mod:rej:{user_id}"))
    return kb.as_markup()


MOD_REJECT_REASONS = {
    "m1": "Вы не являетесь сотрудником компании.",
    "m2": "Некорректные данные в анкете — заполните регистрацию заново.",
    "m3": "Регистрация на марафон уже закрыта.",
    "m4": "Дубликат заявки — вы уже зарегистрированы.",
}


def mod_reject_reason_kb(user_id: int, in_channel: bool, bot_username: str | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for code, text in MOD_REJECT_REASONS.items():
        kb.row(_btn(text[:60], f"mod:rej_r:{user_id}:{code}"))
    if in_channel and bot_username:
        # A channel post has no FSM dialogue, so a free-form reason is typed in the bot's DM.
        kb.row(InlineKeyboardButton(text="✍️ Своя причина (в личке бота)", url=f"https://t.me/{bot_username}?start=modrej_{user_id}"))
    elif not in_channel:
        kb.row(_btn("✍️ Своя причина", f"mod:rej_custom:{user_id}"))
    kb.row(_btn("⬅️ Отмена", f"mod:card:{user_id}"))
    return kb.as_markup()


def channel_review_kb(sub: Submission) -> InlineKeyboardMarkup:
    """Buttons under a report card in the results channel."""
    kb = InlineKeyboardBuilder()
    kb.row(_btn(f"✅ Зачесть +{sub.target_points}", f"adm:ok:{sub.id}"), _btn("❌ Отклонить", f"adm:rej:{sub.id}"))
    return kb.as_markup()


def applications_kb(users: list[User], page: int, per_page: int = 8) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    chunk = users[page * per_page : (page + 1) * per_page]
    for u in chunk:
        kb.row(_btn(f"🙋 {u.display_name[:28]}" + (f" · {u.department[:14]}" if u.department else ""), f"mod:card:{u.id}"))
    nav = []
    if page > 0:
        nav.append(_btn("⬅️", f"adm:apps:{page - 1}"))
    if (page + 1) * per_page < len(users):
        nav.append(_btn("➡️", f"adm:apps:{page + 1}"))
    if nav:
        kb.row(*nav)
    kb.row(_btn("🛠 Панель", "adm"))
    return kb.as_markup()


# ---------- channels and settings ----------

def config_kb(reg_ch: int | None, res_ch: int | None, flags: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn(("✅ " if reg_ch else "⚠️ ") + "Канал заявок", "adm:bind:reg_channel_id"))
    kb.row(_btn(("✅ " if res_ch else "⚠️ ") + "Канал результатов", "adm:bind:results_channel_id"))
    if reg_ch or res_ch:
        kb.row(_btn("🧪 Проверить каналы", "adm:test_ch"))
    kb.row(_btn(("🟢" if flags["registration_open"] else "🔴") + " Приём заявок", "adm:flag:registration_open"))
    kb.row(_btn(("🟢" if flags["submissions_open"] else "🔴") + " Приём отчётов", "adm:flag:submissions_open"))
    kb.row(_btn(("🟢" if flags["moderation_required"] else "🔴") + " Модерация заявок", "adm:flag:moderation_required"))
    kb.row(_btn("🛠 Панель", "adm"))
    return kb.as_markup()


def bind_channel_kb(key: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("🗑 Отвязать канал", f"adm:unbind:{key}"))
    kb.row(_btn("⬅️ Назад", "adm:cfg"))
    return kb.as_markup()


# ---------- broadcast segments ----------

def segments_kb(counts: dict[str, int]) -> InlineKeyboardMarkup:
    from .services import SEGMENTS

    kb = InlineKeyboardBuilder()
    for code, title, _ in SEGMENTS:
        if code in ("lt_n_week", "team"):
            kb.row(_btn(f"{title} →", f"adm:seg_pick:{code}"))
        else:
            kb.row(_btn(f"{title} ({counts.get(code, 0)})", f"adm:seg:{code}:"))
    kb.row(_btn("🛠 Панель", "adm"))
    return kb.as_markup()


def segment_n_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(*[_btn(f"< {n}", f"adm:seg:lt_n_week:{n}") for n in (1, 2, 3, 4)])
    kb.row(_btn("⬅️ Сегменты", "adm:bcast"))
    return kb.as_markup()


def segment_team_kb(teams: list[Team]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for t in teams:
        kb.row(_btn(f"{t.emoji} {t.name}", f"adm:seg:team:{t.id}"))
    kb.row(_btn("⬅️ Сегменты", "adm:bcast"))
    return kb.as_markup()


def week_opened_kb(week: int) -> InlineKeyboardMarkup:
    """Сразу после открытия недели: разослать анонс или вернуться к списку недель."""
    kb = InlineKeyboardBuilder()
    kb.row(_btn(f"📣 Разослать анонс недели {week}", f"adm:announce_ok:{week}", style="primary"))
    kb.row(_btn("⬅️ Недели", "adm:weeks"))
    return kb.as_markup()


def weeks_admin_kb(open_numbers: list[int]) -> InlineKeyboardMarkup:
    """Главный выключатель: включённая неделя показывает свои задания участникам."""
    kb = InlineKeyboardBuilder()
    for w in settings.weeks:
        is_open = w.number in open_numbers
        mark = "🟢" if is_open else "🔴"
        action = "закрыть" if is_open else "открыть"
        kb.row(_btn(f"{mark} Неделя {w.number} — {action}", f"adm:week_toggle:{w.number}"))
    kb.row(_btn("📋 Задания по одному", "adm:tasks"))
    kb.row(_btn("🛠 Панель", "adm"))
    return kb.as_markup()


def tasks_admin_kb(tasks: list[Task]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for t in tasks:
        mark = "🟢" if t.is_active else "🔴"
        kb.row(_btn(f"{mark} нед.{t.week} №{t.code} {t.title[:28]}", f"adm:task_toggle:{t.id}"))
    kb.row(_btn("⬅️ Недели", "adm:weeks"))
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


def reject_reason_kb(sub_id: int, in_channel: bool = False, bot_username: str | None = None) -> InlineKeyboardMarkup:
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
    if in_channel and bot_username:
        # A channel post cannot host a text dialogue — the free-form reason is typed in the bot's DM.
        kb.row(InlineKeyboardButton(text="✍️ Своя причина (в личке бота)", url=f"https://t.me/{bot_username}?start=subrej_{sub_id}"))
    else:
        kb.row(_btn("✍️ Своя причина", f"adm:rej_custom:{sub_id}"))
    kb.row(_btn("⬅️ Назад", f"adm:card:{sub_id}" if in_channel else f"adm:sub:{sub_id}"))
    return kb.as_markup()


REJECT_REASONS = {
    "r1": "На фото нет вас в кадре.",
    "r2": "На фото не видно передачи / процесса выполнения.",
    "r3": "Не хватает фото по условиям зачёта.",
    "r4": "Нет описания / заметки по условиям зачёта.",
    "r5": "Идентичная деятельность выполнена до публикации задания.",
}


USER_STATUS_MARK = {"registered": "", "pending": "⏳ ", "rejected": "❌ ", "disqualified": "🚫 ", "new": "🆕 "}


def users_list_kb(users: list[User], page: int, per_page: int = 8, flt: str = "all", counts: dict | None = None) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if counts is not None:
        from .bot.handlers.admin import USER_FILTERS

        kb.row(*[
            _btn(("• " if k == flt else "") + f"{label} ({counts.get(k, 0)})", f"adm:users:0:{k}")
            for k, label in list(USER_FILTERS.items())[:2]
        ])
        kb.row(*[
            _btn(("• " if k == flt else "") + f"{label} ({counts.get(k, 0)})", f"adm:users:0:{k}")
            for k, label in list(USER_FILTERS.items())[2:]
        ])
    chunk = users[page * per_page : (page + 1) * per_page]
    for u in chunk:
        st = USER_STATUS_MARK.get(u.status.value, "")
        team = f" · {u.team.emoji}" if u.team else " · ❔"
        kb.row(_btn(f"{st}{u.display_name[:28]}{team}", f"adm:user:{u.id}"))
    nav = []
    if page > 0:
        nav.append(_btn("⬅️", f"adm:users:{page - 1}:{flt}"))
    if (page + 1) * per_page < len(users):
        nav.append(_btn("➡️", f"adm:users:{page + 1}:{flt}"))
    if nav:
        kb.row(*nav)
    kb.row(_btn("🔍 Поиск участника", "adm:user_search"))
    kb.row(_btn("🛠 Панель", "adm"))
    return kb.as_markup()


def admin_user_kb(u: User) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if u.status.value in ("pending", "rejected"):
        kb.row(_btn("✅ Принять заявку", f"mod:ok:{u.id}"))
    if u.status.value == "pending":
        kb.row(_btn("❌ Отклонить заявку", f"mod:rej:{u.id}"))
    if u.status.value == "disqualified":
        kb.row(_btn("♻️ Восстановить", f"adm:reinstate:{u.id}"))
    elif u.status.value == "registered":
        kb.row(_btn("🚫 Дисквалифицировать", f"adm:dq:{u.id}"))
    kb.row(_btn("🔀 Перевести в команду", f"adm:move:{u.id}"))
    kb.row(_btn("🗑 Удалить из бота", f"adm:del:{u.id}"))
    kb.row(_btn("⬅️ Участники", "adm:users:0"), _btn("🛠 Панель", "adm"))
    return kb.as_markup()


def user_delete_confirm_kb(u: User) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("🗑 Да, удалить полностью", f"adm:del_ok:{u.id}"))
    kb.row(_btn("⬅️ Отмена", f"adm:user:{u.id}"))
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


def admin_team_manage_kb(team: Team) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("✏️ Переименовать", f"adm:team_rename:{team.id}"))
    kb.row(_btn("🗑 Удалить команду", f"adm:team_del:{team.id}"))
    kb.row(_btn("⬅️ Команды", "adm:teams"), _btn("🛠 Панель", "adm"))
    return kb.as_markup()


def admin_team_del_kb(team: Team) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("🗑 Да, удалить", f"adm:team_del_ok:{team.id}"), _btn("⬅️ Назад", f"adm:team:{team.id}"))
    return kb.as_markup()


def admin_teams_kb(rows: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for r in rows:
        t = r["team"]
        kb.row(_btn(f"{t.emoji} {t.name} ({len(r['members'])}/{settings.team_size}) · {r['points']} б.", f"adm:team:{t.id}"))
    kb.row(_btn("➕ Создать команду", "adm:team_new"))
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
