"""All user-facing texts (HTML parse mode)."""
from __future__ import annotations

from html import escape

from .config import settings
from .models import Submission, SubmissionStatus, Task, Team, User

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
    SubmissionStatus.rejected: "отклонено",
    SubmissionStatus.cancelled: "отменено",
}


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
    return (
        f"<b>🌱 {e(settings.marathon_title)}</b>\n\n"
        "Привет! Это бот марафона добрых дел. Здесь ты:\n"
        "• зарегистрируешься и вступишь в команду,\n"
        "• увидишь задания недели и отправишь отчёты,\n"
        "• будешь следить за баллами команды.\n\n"
        "Перед регистрацией ознакомься с правилами 👇"
    )


def registration_step(step: str, draft: dict) -> str:
    head = "<b>📝 Регистрация</b>\n\n"
    done = ""
    if draft.get("full_name"):
        done += f"👤 Имя: <b>{e(draft['full_name'])}</b>\n"
    if draft.get("department"):
        done += f"🏢 Отдел: <b>{e(draft['department'])}</b>\n"
    if done:
        done += "\n"
    prompts = {
        "full_name": "Шаг 1/3. Напиши свои <b>фамилию и имя</b> сообщением в чат.",
        "department": "Шаг 2/3. Напиши <b>отдел / должность</b> (или нажми «Пропустить»).",
        "city": "Шаг 3/3. Напиши <b>город / офис</b> (или нажми «Пропустить»).",
        "confirm": "Проверь данные и подтверди регистрацию. Нажимая «Подтвердить», ты принимаешь правила марафона и обязуешься дойти до конца 💪",
    }
    return head + done + prompts[step]


def main_menu(user: User, my_points: int, team_points: int | None, team_rank: int | None, week_stats: dict | None) -> str:
    cw = settings.current_week()
    status = settings.marathon_status()
    lines = [f"<b>🌱 {e(settings.marathon_title)}</b>", ""]
    lines.append(f"👤 <b>{e(user.display_name)}</b>" + (f" · {e(user.department)}" if user.department else ""))
    if user.team:
        rank = f" · {team_rank} место" if team_rank else ""
        lines.append(f"👥 Команда: <b>{e(user.team.emoji)} {e(user.team.name)}</b> — {team_points or 0} баллов{rank}")
    else:
        lines.append("👥 Команда: <i>не выбрана</i> ⚠️")
    lines.append(f"⭐ Мои баллы: <b>{my_points}</b>")
    lines.append("")
    if status == "before":
        lines.append(f"🗓 Марафон стартует <b>{settings.weeks[0].start.strftime('%d.%m')}</b>. Успей собрать команду!")
    elif status == "after":
        lines.append("🏁 Марафон завершён. Спасибо за участие!")
    elif cw:
        ws = week_stats or {}
        lines.append(
            f"🗓 Сейчас <b>{cw.number} неделя</b> ({cw.label}). "
            f"Отправлено заданий: <b>{ws.get('submitted', 0)}/4</b>, зачтено: <b>{ws.get('approved', 0)}</b>."
        )
        if ws.get("submitted", 0) == 0:
            lines.append("❗ Минимум 1 задание в неделю — не забудь отправить отчёт.")
    if user.status.value == "disqualified":
        lines.append("\n🚫 <b>Вы дисквалифицированы.</b> Ваши результаты не учитываются в командном зачёте.")
    return "\n".join(lines)


def teams_list(teams: list[dict], user: User) -> str:
    lines = ["<b>👥 Команды</b>", ""]
    if not teams:
        lines.append("Пока нет ни одной команды. Создай первую!")
    for i, row in enumerate(teams, 1):
        t: Team = row["team"]
        n = len(row["members"])
        full = " (заполнена)" if n >= settings.team_size else ""
        mine = " ← ваша" if user.team_id == t.id else ""
        lines.append(f"{i}. {e(t.emoji)} <b>{e(t.name)}</b> — {n}/{settings.team_size}{full} · {row['points']} б.{mine}")
    lines.append("")
    if user.team_id:
        lines.append("Открой команду, чтобы посмотреть состав.")
    else:
        lines.append(f"Выбери команду, в которой есть место, или создай свою. Не можешь выбрать — напиши {e(settings.pc_contact)} через кнопку «Помощь P&C».")
    return "\n".join(lines)


def team_card(team: Team, members: list[User], points: int, rank: int | None, user_pts: dict[int, int], viewer: User) -> str:
    lines = [f"<b>{e(team.emoji)} {e(team.name)}</b>", ""]
    lines.append(f"⭐ Баллы команды: <b>{points}</b>" + (f" · {rank} место" if rank else ""))
    lines.append(f"👥 Состав ({len(members)}/{settings.team_size}):")
    for m in members:
        cap = " 👑" if team.captain_id == m.id else ""
        lines.append(f"  • {e(m.display_name)}{cap} — {user_pts.get(m.id, 0)} б.")
    free = settings.team_size - len(members)
    if free > 0:
        lines.append(f"\nСвободных мест: {free}")
    return "\n".join(lines)


def leaderboard_text(rows: list[dict]) -> str:
    lines = ["<b>🏆 Рейтинг команд</b>", ""]
    medals = ["🥇", "🥈", "🥉"]
    if not rows:
        lines.append("Команд пока нет.")
    for i, r in enumerate(rows):
        t: Team = r["team"]
        medal = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{medal} {e(t.emoji)} <b>{e(t.name)}</b> — <b>{r['points']}</b> б. ({len(r['members'])} чел.)")
    return "\n".join(lines)


def tasks_list(week: int, tasks: list[Task], subs: dict[int, Submission]) -> str:
    w = settings.week(week)
    cw = settings.current_week()
    lines = [f"<b>📋 Задания — {week} неделя</b> ({w.label if w else ''})", ""]
    if cw and cw.number == week:
        lines.append("🟢 Неделя активна: отчёты принимаются до " + settings.week_deadline(week).strftime("%d.%m %H:%M"))
    elif cw and cw.number > week or settings.marathon_status() == "after":
        lines.append("⚪ Неделя завершена, отчёты не принимаются.")
    else:
        lines.append("🔒 Задания откроются в свою неделю.")
    lines.append("")
    for t in tasks:
        sub = subs.get(t.id)
        st = f" {STATUS_ICON[sub.status]} {STATUS_LABEL[sub.status]}" if sub and sub.status != SubmissionStatus.cancelled else ""
        lines.append(f"{e(t.emoji)} <b>№{t.code}. {e(t.title)}</b> — {t.points_label} б.{st}")
    lines.append("\nМинимум 1 задание в неделю, максимум 4. Открой задание, чтобы прочитать условия зачёта.")
    return "\n".join(lines)


def task_card(task: Task, sub: Submission | None) -> str:
    lines = [f"{e(task.emoji)} <b>Задание №{task.code}. {e(task.title)}</b>", f"🗓 {task.week} неделя · ⭐ {task.points_label} баллов", ""]
    lines.append(e(task.description))
    lines.append("")
    lines.append("<b>Условия зачёта:</b>")
    lines.append(e(task.conditions))
    if task.options:
        for o in task.options:
            lines.append(f"\n<b>▸ {e(o.title)} — {o.points} б.</b>\n{e(o.conditions)}")
    if sub and sub.status != SubmissionStatus.cancelled:
        lines.append("")
        opt = f" (опция: {e(sub.option.title)})" if sub.option else ""
        lines.append(f"{STATUS_ICON[sub.status]} Статус: <b>{STATUS_LABEL[sub.status]}</b>{opt}")
        if sub.status == SubmissionStatus.approved:
            lines.append(f"Начислено: <b>{sub.points_awarded}</b> б.")
        if sub.status == SubmissionStatus.rejected and sub.review_comment:
            lines.append(f"Комментарий P&C: <i>{e(sub.review_comment)}</i>")
    return "\n".join(lines)


def submission_editor(sub: Submission) -> str:
    task = sub.task
    files = len(sub.files or [])
    lines = [f"📤 <b>Отчёт: №{task.code}. {e(task.title)}</b>"]
    if sub.option:
        lines.append(f"Опция: <b>{e(sub.option.title)}</b> — {sub.option.points} б.")
    lines.append("")
    lines.append("<b>Что нужно:</b>")
    lines.append(e(sub.option.conditions if sub.option else task.conditions))
    lines.append("")
    ok_f = "✅" if files >= sub.required_photos else "◻️"
    lines.append(f"{ok_f} Файлы: <b>{files}</b> (нужно минимум {sub.required_photos}) — просто отправь фото/файлы в чат")
    if task.note_required:
        ok_n = "✅" if sub.note else "◻️"
        lines.append(f"{ok_n} Заметка: " + (f"<i>{e(sub.note[:200])}</i>" if sub.note else "отправь текст сообщением"))
    else:
        lines.append("◻️ Заметка (необязательно): " + (f"<i>{e(sub.note[:200])}</i>" if sub.note else "можно отправить текстом"))
    lines.append("\nКогда всё готово — нажми «Отправить на проверку».")
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
    lines = [f"<b>📊 Мои результаты</b> — {total} б.", ""]
    for w in settings.weeks:
        ws = [x for x in subs if x.week == w.number and x.status != SubmissionStatus.cancelled]
        lines.append(f"<b>{w.number} неделя</b> ({w.label})")
        if not ws:
            lines.append("  — отчётов нет")
        for x in ws:
            pts = f" +{x.points_awarded}" if x.status == SubmissionStatus.approved else ""
            lines.append(f"  {STATUS_ICON[x.status]} №{x.task.code} {e(x.task.title)} — {STATUS_LABEL[x.status]}{pts}")
        lines.append("")
    return "\n".join(lines).rstrip()


def week_announce(week: int, tasks: list[Task]) -> str:
    w = settings.week(week)
    lines = [f"📌 <b>Неделя {week}</b> ({w.label if w else ''})", ""]
    intro = {
        1: "🚀 Добрые дела начинаются! На этой неделе тебя ждут 4 задания:",
        2: "✨ Вторая неделя марафона = новые добрые поступки! Что попробуешь на этот раз?",
        3: "🔥 Финальная неделя — время для новой порции вдохновения! Тебя ждут задания:",
    }.get(week, "Новые задания недели:")
    lines.append(intro)
    for t in tasks:
        lines.append(f"{e(t.emoji)} №{t.code}. {e(t.title)} — {t.points_label} б.")
    lines.append("")
    lines.append(f"Отчёты принимаются до <b>{settings.week_deadline(week).strftime('%d.%m %H:%M')}</b>. Минимум 1 задание в неделю! Открой «Задания» в меню 👇")
    return "\n".join(lines)


def week_reminder(week: int) -> str:
    return (
        f"⏰ <b>Напоминание</b>\n\nНеделя {week} заканчивается <b>{settings.week_deadline(week).strftime('%d.%m %H:%M')}</b>, "
        "а у тебя ещё нет отправленных отчётов. По правилам нужно выполнить минимум 1 задание в неделю. "
        "Открой «Задания» и отправь отчёт 💪"
    )
