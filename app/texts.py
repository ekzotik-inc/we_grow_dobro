"""All user-facing texts (HTML parse mode)."""
from __future__ import annotations

from html import escape

from .config import settings
from .emoji import e as px
from .emoji import plain
from .models import Submission, SubmissionStatus, Task, Team, User, UserStatus

STATUS_ICON = {
    SubmissionStatus.draft: "📝",
    SubmissionStatus.pending: "⏳",
    SubmissionStatus.approved: "✅",
    SubmissionStatus.rejected: "❌",
    SubmissionStatus.cancelled: "🚫",
}
STATUS_LABEL = {
    SubmissionStatus.draft: "черновик",
    SubmissionStatus.pending: "на проверке",
    SubmissionStatus.approved: "зачтено",
    SubmissionStatus.rejected: "не зачтено",
    SubmissionStatus.cancelled: "отменено",
}

# Human wording for participant statuses — the raw enum values must never reach a message.
USER_STATUS_TEXT = {
    UserStatus.new: "не заполнил анкету",
    UserStatus.pending: "ждёт решения",
    UserStatus.registered: "участвует",
    UserStatus.rejected: "заявка отклонена",
    UserStatus.disqualified: "дисквалифицирован",
}
MARATHON_STATUS_TEXT = {"before": "ещё не начался", "active": "идёт", "after": "завершён"}


def voice(line: str) -> str:
    """A line spoken by the bot's character, set apart from the data above it."""
    return f"{px('hug')} <b>{e(settings.voice_name)}</b>\n<blockquote>{line}</blockquote>"


def plural(n: int, one: str, few: str, many: str) -> str:
    """Russian noun agreement: 1 задание, 2 задания, 5 заданий."""
    if 11 <= n % 100 <= 14:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def e(text: str | None) -> str:
    return escape(text or "")


RULES = f"""<b>📜 Правила марафона</b>

1. Участие могут принять все сотрудники.
2. Регистрация и распределение участников осуществляется в боте.
3. Количество участников в команде — {settings.team_size}.
4. Если участник не может выбрать для себя команду, он обращается к сотруднику P&C, и тот помогает с распределением.
5. Присоединяясь к марафону, участник обязуется принимать участие в нём и дойти до конца.
6. Период проведения: {' · '.join(f'{w.number} неделя — {w.label}' for w in settings.weeks)}.
7. Каждую неделю анонсируются 4 задания, за каждое можно получить от 200 до 500 баллов. В заданиях с 2 опциями баллы зависят от выбранной опции.
8. В заданиях с опцией выполняется исключительно одна опция.
9. Выполнение заданий возможно только в зафиксированную для них неделю.
10. Минимум выполненных заданий в неделю — 1, максимум — 4.
11. К каждому заданию даны условия зачёта. Задание считается выполненным только при выполнении всех действий.
12. Проверкой условий (скриншоты, фото, описания) занимается сотрудник отдела P&C.
13. При выявлении нарушений правил участник дисквалифицируется, и его результаты не учитываются в командном зачёте."""


def welcome(user: User) -> str:
    first = (user.full_name or "").split()[0] if user.full_name else ""
    hello = f"Привет, {e(first)}!" if first else "Привет!"
    return (
        f"{px('heart')} <b>{e(settings.marathon_title)}</b>\n\n"
        f"{hello} Три недели мы вместе делаем добрые дела — по-настоящему, "
        "а не для галочки. Каждую неделю четыре задания на выбор, команда из "
        f"{settings.team_size} человек и общий зачёт.\n\n"
        f"{voice('Загляни в правила — там всё честно и коротко. Потом жми «Участвовать», и начнём ' + plain('rocket'))}"
    )


def registration_step(step: str, draft: dict) -> str:
    done = []
    if draft.get("full_name"):
        done.append(f"Имя: <b>{e(draft['full_name'])}</b>")
    if draft.get("phone"):
        done.append(f"Телефон: <b>{e(draft['phone'])}</b>")
    if draft.get("team_name"):
        done.append(f"Команда: <b>{e(draft['team_name'])}</b>")
    header = f"{px('rocket')} <b>Заявка на участие</b>"
    filled = ("\n" + "\n".join(done)) if done else ""

    prompts = {
        "full_name": ("Шаг 1 из 3", "Напиши имя и фамилию — так тебя увидят в команде и рейтинге."),
        "phone": ("Шаг 2 из 3", "Оставь номер телефона. Он нужен сотруднику P&C, чтобы связаться с тобой "
                                "по заданиям. Можно нажать кнопку и поделиться номером из Telegram."),
        "team": ("Шаг 3 из 3", "Выбери команду, в которой хочешь участвовать. "
                               "Окончательно распределяет сотрудник P&C — он учтёт твой выбор."),
        "confirm": ("Последний шаг", "Проверь данные. Отправляя заявку, ты принимаешь правила "
                                     "и обязуешься пройти марафон до конца."),
    }
    title, body = prompts[step]
    return f"{header}{filled}\n\n<b>{title}</b>\n{body}"


MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _date_ru(d) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


def main_menu(user: User, my_points: int, team_points: int | None, team_rank: int | None, week_stats: dict | None) -> str:
    cw = settings.current_week()
    status = settings.marathon_status()
    total_teams = week_stats.get("total_teams") if week_stats else None

    lines = [f"{px('heart')} <b>{e(user.display_name)}</b>"]
    if user.department:
        lines.append(f"<i>{e(user.department)}</i>")

    if user.team:
        place = f" · {px('medal')} {team_rank} место из {total_teams}" if team_rank and total_teams else ""
        lines.append(f"🌱 Команда: <b>{e(user.team.emoji)} {e(user.team.name)}</b>{place}")
        lines.append(f"{px('bolt')} Баллы команды: <b>{team_points or 0}</b>")
    else:
        lines.append("🌱 Команда: пока не выбрана")

    approved = (week_stats or {}).get("approved_total", 0)
    lines.append(
        f"⭐ Мой вклад: <b>{my_points}</b> · "
        f"{approved} {plural(approved, 'задание', 'задания', 'заданий')} зачтено"
    )

    if user.status == UserStatus.disqualified:
        lines.append("")
        lines.append(f"{px('blocked')} Твои результаты сняты с командного зачёта. Вопросы — к сотруднику P&C.")
        return "\n".join(lines)

    lines.append("")
    if status == "before":
        start = settings.weeks[0].start
        lines.append(f"🗓 Старт {_date_ru(start)}")
        if user.team:
            line = "Команда есть, правила знаешь — ждём старта. Осталось совсем немного!"
        else:
            line = "Успей выбрать команду до старта — без неё задания не открыть."
        lines.append("")
        lines.append(voice(line))
        return "\n".join(lines)

    if status == "after":
        lines.append("🏁 Марафон завершён")
        lines.append("")
        lines.append(voice(f"Спасибо, что дошёл до конца. Это были славные три недели {plain('heart')}"))
        return "\n".join(lines)

    ws = week_stats or {}
    sent, ok = ws.get("submitted", 0), ws.get("approved", 0)
    deadline = settings.week_deadline(cw.number)
    lines.append(f"🗓 Неделя {cw.number} · до {_date_ru(deadline.date())}")
    lines.append(f"📋 Сдано {sent} из 4 · зачтено {ok}")
    lines.append("")

    if not user.team:
        line = "Сначала выбери команду — задания открываются только участникам команд."
    elif sent == 0:
        line = "На этой неделе ещё ни одного дела. Загляни в задания — там есть простые, на десять минут."
    elif sent >= 4:
        line = f"Все четыре задания сданы. Это максимум за неделю, снимаю шляпу {plain('cool')}"
    else:
        left = 4 - sent
        line = f"Хороший темп! Можно взять ещё {left} {plural(left, 'задание', 'задания', 'заданий')} на этой неделе."
    lines.append(voice(line))
    return "\n".join(lines)


def teams_list(teams: list[dict], user: User) -> str:
    lines = ["<b>🌱 Команды марафона</b>", ""]
    if not teams:
        lines.append("Пока пусто — твоя команда может стать первой.")
    for row in teams:
        t: Team = row["team"]
        n = len(row["members"])
        free = settings.team_size - n
        mark = " ← ты здесь" if user.team_id == t.id else ("" if free > 0 else " · мест нет")
        lines.append(f"{e(t.emoji)} <b>{e(t.name)}</b> — {n} из {settings.team_size} · {row['points']} б.{mark}")
    lines.append("")
    if user.team_id:
        lines.append(voice("Открой любую команду, чтобы посмотреть состав и счёт."))
    else:
        lines.append(voice(
            "Выбирай команду, где есть места, или собери свою. Если сложно определиться — "
            f"нажми «Помощь», и {e(settings.pc_contact)} подберёт команду за тебя."
        ))
    return "\n".join(lines)


def team_card(team: Team, members: list[User], points: int, rank: int | None, user_pts: dict[int, int], viewer: User) -> str:
    lines = [f"<b>{e(team.emoji)} {e(team.name)}</b>", ""]
    lines.append(f"{px('bolt')} <b>{points}</b> баллов" + (f" · {px('medal')} {rank} место" if rank else ""))
    lines.append("")
    for m in sorted(members, key=lambda x: -user_pts.get(x.id, 0)):
        cap = " 👑" if team.captain_id == m.id else ""
        you = " (это ты)" if m.id == viewer.id else ""
        lines.append(f"· {e(m.display_name)}{cap}{you} — {user_pts.get(m.id, 0)} б.")
    free = settings.team_size - len(members)
    lines.append("")
    if free > 0:
        lines.append(f"Свободных мест: {free} из {settings.team_size}")
    else:
        lines.append("Команда в полном составе")
    return "\n".join(lines)


def leaderboard_text(rows: list[dict]) -> str:
    lines = ["🏆 <b>Рейтинг команд</b>", ""]
    medals = [px("medal"), "🥈", "🥉"]
    if not rows:
        lines.append("Счёт пока не открыт — всё впереди.")
    for i, r in enumerate(rows):
        t: Team = r["team"]
        mark = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{mark} {e(t.emoji)} <b>{e(t.name)}</b> — {r['points']} б.")
    if rows:
        lines.append("")
        top = rows[0]
        if len(rows) == 1:
            lines.append(voice("Пока в марафоне одна команда. Позовите коллег — вместе интереснее."))
        elif top["points"] > rows[1]["points"]:
            gap = top["points"] - rows[1]["points"]
            lines.append(voice(f"Впереди «{e(top['team'].name)}» с отрывом в {gap} б. Догнать вполне реально."))
        else:
            lines.append(voice("Идёте вровень — каждое дело может перевернуть таблицу."))
    return "\n".join(lines)


def tasks_list(week: int, tasks: list[Task], subs: dict[int, Submission]) -> str:
    cw = settings.current_week()
    is_now = bool(cw and cw.number == week)
    is_past = bool(cw and cw.number > week) or settings.marathon_status() == "after"

    lines = [f"<b>📋 Неделя {week}</b>"]
    if is_now:
        lines.append(f"Принимаем до {_date_ru(settings.week_deadline(week).date())}")
    elif is_past:
        lines.append("Неделя закрыта")
    else:
        lines.append("Откроется в свой срок")
    lines.append("")

    for task in tasks:
        sub = subs.get(task.id)
        mark = ""
        if sub and sub.status != SubmissionStatus.cancelled:
            mark = f" · {STATUS_ICON[sub.status]} {STATUS_LABEL[sub.status]}"
        lines.append(f"{e(task.emoji)} <b>{e(task.title)}</b>")
        lines.append(f"    {task.points_label} б.{mark}")

    lines.append("")
    if is_now:
        done = len([s for s in subs.values() if s.week == week and s.status in (SubmissionStatus.pending, SubmissionStatus.approved)])
        if done == 0:
            lines.append(voice("Возьми хотя бы одно — этого уже достаточно, чтобы неделя засчиталась."))
        elif done >= 4:
            lines.append(voice(f"Четыре из четырёх. Больше на этой неделе не нужно {plain('cool')}"))
        else:
            lines.append(voice("Открой задание, чтобы посмотреть, что нужно приложить к отчёту."))
    return "\n".join(lines)


def task_card(task: Task, sub: Submission | None) -> str:
    lines = [f"{e(task.emoji)} <b>{e(task.title)}</b>", f"{px('bolt')} {task.points_label} б. · неделя {task.week}", ""]
    lines.append(e(task.description))
    lines.append("")
    if task.options:
        # Full checklists live in the report screen; the card stays short enough for a photo caption.
        lines.append("<b>Один вариант на выбор — оба сделать нельзя</b>")
        for o in task.options:
            lines.append(f"· {e(o.title)} — {o.points} б.")
        lines.append("")
        lines.append("Что приложить — покажу, когда выберешь вариант.")
    else:
        lines.append("<b>Что приложить к отчёту</b>")
        lines.append(e(task.conditions))
    if sub and sub.status != SubmissionStatus.cancelled:
        lines.append("")
        opt = f" · {e(sub.option.title)}" if sub.option else ""
        lines.append(f"{STATUS_ICON[sub.status]} {STATUS_LABEL[sub.status].capitalize()}{opt}")
        if sub.status == SubmissionStatus.approved:
            lines.append(voice(f"Зачтено, {sub.points_awarded} б. ушли команде. Спасибо {plain('heart')}"))
        elif sub.status == SubmissionStatus.rejected and sub.review_comment:
            lines.append(f"Причина: <i>{e(sub.review_comment)}</i>")
            lines.append(voice("Это поправимо — доснимай, что просят, и отправляй снова."))
        elif sub.status == SubmissionStatus.pending:
            lines.append(voice("Отчёт у сотрудника P&C. Как проверят — сразу напишу."))
    return "\n".join(lines)


def submission_editor(sub: Submission) -> str:
    task = sub.task
    files = len(sub.files or [])
    need = sub.required_photos

    lines = [f"📤 <b>{e(task.title)}</b>"]
    if sub.option:
        lines.append(f"Вариант: {e(sub.option.title)} — {sub.option.points} б.")
    lines.append("")
    lines.append("<b>Что приложить</b>")
    lines.append(e(sub.option.conditions if sub.option else task.conditions))
    lines.append("")

    ok_f = "✅" if files >= need else "◻️"
    if files >= need:
        lines.append(f"{ok_f} Фото и файлы: {files} — достаточно")
    else:
        lines.append(f"{ok_f} Фото и файлы: {files} из {need} — просто пришли их сюда")

    note_mark = "✅" if sub.note else ("◻️" if task.note_required else "○")
    if sub.note:
        lines.append(f"{note_mark} Заметка: <i>{e(sub.note[:180])}</i>")
    elif task.note_required:
        lines.append(f"{note_mark} Заметка: напиши пару строк сообщением")
    else:
        lines.append(f"{note_mark} Заметка: по желанию")

    lines.append("")
    missing = []
    if files < need:
        missing.append(f"{need - files} {plural(need - files, 'файл', 'файла', 'файлов')}")
    if task.note_required and not sub.note:
        missing.append("заметку")
    if missing:
        lines.append(voice("Осталось добавить " + " и ".join(missing) + " — и можно отправлять."))
    else:
        lines.append(voice(f"Всё на месте. Жми «Отправить на проверку» {plain('rocket')}"))
    return "\n".join(lines)


def submission_admin_card(sub: Submission, idx: int | None = None, total: int | None = None) -> str:
    u = sub.user
    task = sub.task
    head = f"<b>🔎 Проверка отчёта #{sub.id}</b>" + (f" ({idx}/{total})" if idx else "")
    lines = [head, ""]
    lines.append(f"👤 {e(u.display_name)}" + (f" (@{e(u.username)})" if u.username else "") + (f" · {e(u.department)}" if u.department else ""))
    lines.append(f"👥 Команда: {e(u.team.emoji + ' ' + u.team.name) if u.team else '—'}")
    lines.append(f"📋 Задание №{task.code}: <b>{e(task.title)}</b>" + (f" · опция «{e(sub.option.title)}»" if sub.option else ""))
    lines.append(f"⭐ К начислению: <b>{sub.target_points}</b> б. · неделя {sub.week}")
    lines.append(f"📎 Файлов: {len(sub.files or [])}" + (f" · отправлено {sub.submitted_at.strftime('%d.%m %H:%M')}" if sub.submitted_at else ""))
    lines.append("")
    lines.append("<b>Заметка участника:</b>")
    lines.append(e(sub.note) if sub.note else "<i>нет</i>")
    lines.append("")
    lines.append("<b>Условия зачёта:</b>")
    lines.append(e(sub.option.conditions if sub.option else task.conditions))
    lines.append(f"\n{STATUS_ICON[sub.status]} Статус: {STATUS_LABEL[sub.status]}")
    return "\n".join(lines)


def my_results(user: User, subs: list[Submission], total: int) -> str:
    done = len([x for x in subs if x.status == SubmissionStatus.approved])
    lines = [f"{px('bolt')} <b>Мой вклад: {total} б.</b>",
             f"{done} {plural(done, 'доброе дело', 'добрых дела', 'добрых дел')} зачтено", ""]
    for w in settings.weeks:
        ws = [x for x in subs if x.week == w.number and x.status != SubmissionStatus.cancelled]
        lines.append(f"<b>Неделя {w.number}</b>")
        if not ws:
            lines.append("    пусто")
        for x in ws:
            pts = f" +{x.points_awarded}" if x.status == SubmissionStatus.approved else ""
            lines.append(f"    {STATUS_ICON[x.status]} {e(x.task.title)}{pts}")
        lines.append("")
    if total == 0:
        lines.append(voice("Пока чисто — но это легко исправить одним делом на десять минут."))
    else:
        lines.append(voice(f"Каждый твой балл ушёл команде. Так держать {plain('heart')}"))
    return "\n".join(lines).rstrip()


def week_announce(week: int, tasks: list[Task]) -> str:
    intro = {
        1: f"{px('rocket')} <b>Марафон начался!</b>",
        2: f"{px('star_eyes')} <b>Вторая неделя открыта</b>",
        3: f"{px('bolt')} <b>Финальная неделя</b>",
    }.get(week, f"<b>Неделя {week}</b>")
    lines = [intro, "", "Четыре дела на выбор:"]
    for task in tasks:
        lines.append(f"{e(task.emoji)} {e(task.title)} — {task.points_label} б.")
    lines.append("")
    lines.append(f"Успеть можно до {_date_ru(settings.week_deadline(week).date())}.")
    lines.append("")
    closing = {
        1: "Начни с любого — важно просто начать.",
        2: "Первая неделя позади. Вторая обычно даётся легче.",
        3: "Последний рывок. Ещё есть время подтянуть команду в таблице.",
    }.get(week, "Открывай «Задания» и выбирай.")
    lines.append(voice(closing))
    return "\n".join(lines)


def week_reminder(week: int) -> str:
    return (
        f"⏰ <b>Неделя {week} заканчивается завтра</b>\n\n"
        "У тебя пока нет ни одного отправленного дела на этой неделе.\n\n"
        + voice("Есть задания на десять минут — угостить коллегу кофе, написать благодарность. "
                f"Успеть реально прямо сегодня {plain('hug')}")
    )


# ---------- channel cards ----------

USER_STATUS_LINE = {
    UserStatus.new: "🆕 не завершил регистрацию",
    UserStatus.pending: "⏳ ожидает решения P&C",
    UserStatus.registered: "✅ принят",
    UserStatus.rejected: "❌ заявка отклонена",
    UserStatus.disqualified: "🚫 дисквалифицирован",
}


def registration_channel_card(user: User) -> str:
    """Application card published in the registration channel."""
    lines = ["🙋 <b>Заявка на участие в марафоне</b>", ""]
    lines.append(f"👤 <b>{e(user.display_name)}</b>" + (f" (@{e(user.username)})" if user.username else ""))
    if user.phone:
        lines.append(f"📞 {e(user.phone)}")
    if user.department:
        lines.append(f"🏢 {e(user.department)}")
    lines.append(f"🆔 <code>{user.tg_id}</code>")
    if user.rules_accepted_at:
        lines.append(f"📜 Правила приняты: {user.rules_accepted_at.strftime('%d.%m.%Y %H:%M')} UTC")
    lines.append("")
    if user.status == UserStatus.pending:
        lines.append("⏳ <b>Ожидает решения.</b> Примите или отклоните заявку кнопками ниже.")
        lines.append("При приёме участник попадёт в выбранную команду, если там есть место.")
    elif user.status == UserStatus.registered:
        who = f" (P&C id {user.moderated_by})" if user.moderated_by else ""
        when = f" · {user.moderated_at.strftime('%d.%m %H:%M')}" if user.moderated_at else ""
        lines.append(f"✅ <b>Принят{when}</b>{who}")
        if user.team:
            lines.append(f"👥 Команда: {e(user.team.emoji)} {e(user.team.name)}")
    elif user.status == UserStatus.rejected:
        when = f" · {user.moderated_at.strftime('%d.%m %H:%M')}" if user.moderated_at else ""
        lines.append(f"❌ <b>Отклонён{when}</b>")
        lines.append(f"Причина: <i>{e(user.reject_reason or 'не указана')}</i>")
    elif user.status == UserStatus.disqualified:
        lines.append(f"🚫 <b>Дисквалифицирован.</b> Причина: <i>{e(user.disqualified_reason or '—')}</i>")
    return "\n".join(lines)


def submission_channel_card(sub: Submission) -> str:
    """Report card published in the results channel (media is sent right above it)."""
    u = sub.user
    task = sub.task
    lines = [f"📤 <b>Отчёт #{sub.id}</b> · неделя {sub.week}", ""]
    lines.append(f"👤 {e(u.display_name)}" + (f" (@{e(u.username)})" if u.username else ""))
    lines.append(f"👥 Команда: {e(u.team.emoji + ' ' + u.team.name) if u.team else '—'}")
    lines.append(f"📋 Задание №{task.code}: <b>{e(task.title)}</b>" + (f" · опция «{e(sub.option.title)}»" if sub.option else ""))
    lines.append(f"⭐ К начислению: <b>{sub.target_points}</b> б. · 📎 файлов: {len(sub.files or [])}")
    lines.append("")
    lines.append("<b>Заметка участника:</b>")
    lines.append(e(sub.note) if sub.note else "<i>нет</i>")
    lines.append("")
    lines.append("<b>Условия зачёта:</b>")
    lines.append(e(sub.option.conditions if sub.option else task.conditions))
    lines.append("")
    if sub.status == SubmissionStatus.pending:
        lines.append("⏳ <b>Ожидает проверки.</b> Баллы начисляются только после «Зачесть».")
    elif sub.status == SubmissionStatus.approved:
        when = f" · {sub.reviewed_at.strftime('%d.%m %H:%M')}" if sub.reviewed_at else ""
        lines.append(f"✅ <b>Зачтено{when}</b> — начислено {sub.points_awarded} б. (P&C id {sub.reviewed_by})")
    elif sub.status == SubmissionStatus.rejected:
        when = f" · {sub.reviewed_at.strftime('%d.%m %H:%M')}" if sub.reviewed_at else ""
        lines.append(f"❌ <b>Отклонено{when}</b> (P&C id {sub.reviewed_by})")
        lines.append(f"Причина: <i>{e(sub.review_comment or 'не указана')}</i>")
    elif sub.status == SubmissionStatus.cancelled:
        lines.append("🚫 <b>Отчёт отозван участником.</b>")
    return "\n".join(lines)


def pending_status(user: User) -> str:
    """What a not-yet-approved participant sees instead of the main menu."""
    if user.status == UserStatus.pending:
        return (
            f"{px('thinking')} <b>Анкета отправлена</b>\n\n"
            f"{e(user.display_name)}"
            + (f" · {e(user.department)}" if user.department else "")
            + "\n\n"
            + voice(f"Заявку посмотрит {e(settings.pc_contact)} и подтвердит участие. "
                    "Как только это случится — напишу сюда, и откроется меню с заданиями.")
        )
    return (
        f"{px('blocked')} <b>Заявка отклонена</b>\n\n"
        f"Причина: <i>{e(user.reject_reason or 'не указана')}</i>\n\n"
        + voice("Если это недоразумение — напиши сотруднику P&C, разберёмся. "
                "Или заполни анкету заново, если что-то было указано неверно.")
    )


def broadcast_preview(segment_title: str, recipients: int, body: str) -> str:
    return (
        f"✉️ <b>Предпросмотр рассылки</b>\n\n"
        f"Сегмент: <b>{e(segment_title)}</b>\n"
        f"Получателей: <b>{recipients}</b>\n"
        f"{'─' * 20}\n\n{body}"
    )


def db_expiry_warning() -> str | None:
    """Free hosted databases are deleted on a deadline — warn P&C while there is still time to export."""
    days = settings.db_days_left()
    if days is None or days > 7:
        return None
    when = settings.db_expiry_date.strftime("%d.%m.%Y")
    if days < 0:
        return f"🔴 <b>База данных должна была быть удалена {when}.</b> Срочно выгрузите итоги: /admin → 📥 Экспорт Excel."
    when_txt = "сегодня" if days == 0 else f"через {days} дн. ({when})"
    return (
        f"🔴 <b>Внимание: база данных будет удалена {when_txt}.</b>\n"
        "Выгрузите итоги марафона, пока данные на месте: /admin → 📥 Экспорт Excel."
    )


def submission_sent(task: Task) -> str:
    return (
        f"{px('salute')} <b>Отчёт отправлен</b>\n"
        f"{e(task.title)}\n\n"
        + voice("Сотрудник P&C проверит и начислит баллы. Как решит — сразу напишу сюда.")
    )


# ---------- pushes a participant gets after a P&C action ----------

def push_approved(user: User) -> str:
    team = f"\nКоманда: <b>{e(user.team.emoji)} {e(user.team.name)}</b>" if user.team else ""
    return (
        f"{px('star_eyes')} <b>Заявка принята</b>{team}\n\n"
        + voice("Добро пожаловать в марафон! Открывай задания недели — там четыре дела на выбор.")
    )


def push_team_assigned(user: User, old_name: str | None) -> str:
    moved = f" вместо «{e(old_name)}»" if old_name else ""
    return (
        f"{px('hug')} <b>Ты в команде {e(user.team.emoji)} {e(user.team.name)}</b>{moved}\n\n"
        + voice("Теперь твои баллы идут в её копилку. Загляни в состав — познакомься с ребятами.")
    )


def push_team_removed() -> str:
    return (
        f"{px('thinking')} <b>Ты пока вне команды</b>\n\n"
        + voice("Сотрудник P&C подберёт новую. Задания на это время поставлены на паузу.")
    )


def push_team_renamed(old: str, team: Team) -> str:
    return (
        f"{px('shh')} <b>Команда переименована</b>\n"
        f"«{e(old)}» → <b>{e(team.emoji)} {e(team.name)}</b>\n\n"
        + voice("Название новое, счёт прежний.")
    )


def push_team_disbanded() -> str:
    return (
        f"{px('thinking')} <b>Команда расформирована</b>\n\n"
        + voice("Баллы сохранены. Сотрудник P&C переведёт тебя в другую команду.")
    )


def push_disqualified(reason: str) -> str:
    return (
        f"{px('blocked')} <b>Ты снят с марафона</b>\n"
        f"Причина: <i>{e(reason)}</i>\n\n"
        + voice("Результаты больше не идут в командный зачёт. Если это ошибка — напиши сотруднику P&C.")
    )


def push_reinstated() -> str:
    return (
        f"{px('salute')} <b>Участие восстановлено</b>\n\n"
        + voice("Баллы снова в командном зачёте. Продолжаем!")
    )


def top_digest(rows: list[dict], people: list[tuple[str, str, int]]) -> str:
    """Periodic standings: teams first, then the strongest participants."""
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{px('medal')} <b>Как идут дела</b>", "", "<b>Команды</b>"]
    for i, r in enumerate(rows[:5]):
        mark = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{mark} {e(r['team'].emoji)} {e(r['team'].name)} — {r['points']} б.")
    if people:
        lines.append("")
        lines.append("<b>Участники</b>")
        for i, (name, team, pts) in enumerate(people[:5]):
            mark = medals[i] if i < 3 else f"{i + 1}."
            lines.append(f"{mark} {e(name)} · {e(team)} — {pts} б.")
    lines.append("")
    lines.append(voice("Одно дело меняет расклад. Загляни в задания недели."))
    return "\n".join(lines)


# Rotated by the scheduler so the nudges never repeat two days running.
MOTIVATION = [
    "Доброе дело не обязано быть большим. Кофе коллеге — тоже дело.",
    "Самое трудное — начать. Дальше идёт само.",
    "Команда считает баллы вместе. Твой отчёт двигает всех.",
    "Есть задания на десять минут. Загляни — вдруг сегодня как раз тот день.",
    "Сделал доброе дело — не забудь отправить отчёт, иначе баллы не дойдут.",
    "Пока неделя идёт, попыток сколько угодно. Отклонили — поправь и пришли снова.",
    "Кто-то сегодня получит твою помощь и запомнит её надолго.",
]


def motivation(index: int) -> str:
    return f"{px('heart')} " + voice(MOTIVATION[index % len(MOTIVATION)])
