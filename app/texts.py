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


def quote(body: str, expandable: bool = False) -> str:
    """A block quote. Expandable ones stay collapsed until tapped — for rules and long checklists."""
    tag = "<blockquote expandable>" if expandable else "<blockquote>"
    return f"{tag}{body}</blockquote>"


def rule(title: str) -> str:
    """A small caps-ish heading used to break a long message into readable parts."""
    return f"<b>{title}</b>"


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


def pc_link() -> str:
    """Имя сотрудника P&C — ссылкой, если известен его Telegram-логин."""
    name = e(settings.pc_contact)
    if settings.pc_username:
        return f'<a href="https://t.me/{e(settings.pc_username)}">{name}</a>'
    return name


RULES = f"""📜 <b>Правила марафона</b>

{voice("Садись поудобнее — расскажу, как всё устроено. Это недолго, зато потом никаких сюрпризов.")}

👥 <b>Кто участвует</b>
Все сотрудники — без отбора и без исключений. Регистрируешься здесь, в боте, а команду подбирает сотрудник P&amp;C: в каждой по {settings.team_size} человек. Если хочешь к конкретным коллегам — скажи через «Помощь», это учтут.

🤝 <b>Одно обещание</b>
Присоединяясь, ты обещаешь дойти до конца. Не идеально, не на все сто — просто до конца. Команда рассчитывает на тебя.

📅 <b>Три недели</b>
{chr(10).join(f'• <b>{w.number} неделя</b> — {w.label}' for w in settings.weeks)}
Каждую неделю открываются <b>4 задания</b>, от <b>200</b> до <b>500</b> баллов за каждое. Задание живёт только в свою неделю: прошлую не догнать, будущую не забежать.

🎯 <b>Сколько брать</b>
Минимум — <b>одно</b> дело в неделю, максимум — <b>все четыре</b>. Есть задания с двумя вариантами: выбираешь <b>только один</b>, второй уже не засчитается.

✅ <b>Что считается выполненным</b>
У каждого задания есть условия зачёта — что именно приложить к отчёту. Дело засчитывается, только когда выполнено всё, что там перечислено.
Фото, скриншоты и описания проверяет сотрудник P&amp;C — баллы приходят после подтверждения.

⛔️ <b>Единственное «нельзя»</b>
Нарушение правил — это дисквалификация, и результаты снимаются с командного зачёта. Но, честно говоря, тут не за что нарушать: марафон про добрые дела.

{voice("Вот и всё. Ниже — те же правила официальным языком, если захочешь сверить.")}

<blockquote expandable>1. Участие могут принять все сотрудники.
2. Регистрация и распределение участников осуществляется в боте.
3. Количество участников в команде — {settings.team_size}.
4. Если участник не может выбрать для себя команду, он обращается к сотруднику P&amp;C, и тот помогает с распределением.
5. Присоединяясь к марафону, участник обязуется принимать участие в нём и дойти до конца.
6. Период проведения: {' · '.join(f'{w.number} неделя — {w.label}' for w in settings.weeks)}.
7. Каждую неделю анонсируются 4 задания, за каждое можно получить от 200 до 500 баллов. В заданиях с 2 опциями баллы зависят от выбранной опции.
8. В заданиях с опцией выполняется исключительно одна опция.
9. Выполнение заданий возможно только в зафиксированную для них неделю.
10. Минимум выполненных заданий в неделю — 1, максимум — 4.
11. К каждому заданию даны условия зачёта. Задание считается выполненным только при выполнении всех действий.
12. Проверкой условий (скриншоты, фото, описания) занимается сотрудник отдела P&amp;C.
13. При выявлении нарушений правил участник дисквалифицируется, и его результаты не учитываются в командном зачёте.</blockquote>"""


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
        "phone": ("Шаг 2 из 3", "Напиши номер телефона сообщением — например, <code>+998 90 123 45 67</code>. "
                                "Он нужен сотруднику P&amp;C, чтобы связаться с тобой по заданиям.\n"
                                "<i>На телефоне можно нажать кнопку «Поделиться номером» внизу. "
                                "В Telegram на компьютере такая кнопка не срабатывает — там просто напиши номер.</i>"),
        "team": ("Шаг 3 из 3", "Выбери команду, в которой хочешь участвовать. "
                               "Окончательно распределяет сотрудник P&amp;C — он учтёт твой выбор."),
        "confirm": ("Последний шаг", "Проверь данные. Отправляя заявку, ты принимаешь правила "
                                     "и обязуешься пройти марафон до конца."),
    }
    title, body = prompts[step]
    return f"{header}{filled}\n\n<b>{title}</b>\n{body}"


MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
          "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _date_ru(d) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


def num(value: int) -> str:
    """208153 -> 208 153. Thin space, so long numbers stay readable in a card."""
    return f"{value:,}".replace(",", "\u2009")


def row(icon: str, label: str, value: str, extra: str = "") -> str:
    """One line of a card: icon, label, bold value, optional secondary part after a dot."""
    tail = f" · {extra}" if extra else ""
    return f"{icon} {label}: <b>{value}</b>{tail}"


def main_menu(user: User, my_points: int, team_points: int | None, team_rank: int | None, week_stats: dict | None) -> str:
    from . import services  # локальный импорт: services знает, какие недели открыл админ

    cw = services.current_week()
    total_teams = week_stats.get("total_teams") if week_stats else None
    ws = week_stats or {}

    lines = [f"👤 <b>{e(user.display_name)}</b>"]
    if user.department:
        lines.append(f"<i>{e(user.department)}</i>")
    lines.append("")

    if user.team:
        rank = f"🏆 #{team_rank} из {total_teams}" if team_rank and total_teams else ""
        lines.append(row("🌱", "Команда", f"{e(user.team.emoji)} {e(user.team.name)}", rank))
        lines.append(row("⚡", "Баллы команды", num(team_points or 0)))

    approved = ws.get("approved_total", 0)
    place = f"место #{ws['my_rank']} из {ws['total_users']}" if ws.get("my_rank") and ws.get("total_users") else ""
    lines.append(row("⭐", "Мои баллы", num(my_points), place))
    lines.append(row("✅", "Зачтено дел", str(approved)))

    if user.status == UserStatus.disqualified:
        lines.append("")
        lines.append(f"{px('blocked')} Твои результаты сняты с командного зачёта. Вопросы — через «Помощь».")
        return "\n".join(lines)

    lines.append("")
    if cw is None:
        lines.append("📅 <b>Задания скоро откроются</b>")
        lines.append("")
        lines.append(voice("Неделя ещё не началась. Как только откроется — пришлю список заданий сюда.\n"
                           "Пока загляни в «Правила», чтобы ничего не упустить."))
        return "\n".join(lines)

    sent, ok = ws.get("submitted", 0), ws.get("approved", 0)
    lines.append(row("📅", f"Неделя {cw.number}", f"до {_date_ru(settings.week_deadline(cw.number).date())}"))
    lines.append(row("📤", "Сдано за неделю", f"{sent} из 4", f"зачтено {ok}"))
    lines.append("")

    if sent == 0:
        line = "На этой неделе ещё ни одного дела. Загляни в задания — там есть простые, на десять минут."
    elif sent >= 4:
        line = f"Все четыре задания сданы. Это максимум за неделю, снимаю шляпу {plain('cool')}"
    else:
        left = 4 - sent
        line = f"Хороший темп! Можно взять ещё {left} {plural(left, 'задание', 'задания', 'заданий')} на этой неделе."
    lines.append(voice(line + "\nЖми «Задания недели» — покажу, что осталось."))
    return "\n".join(lines)


def teams_list(teams: list[dict], user: User) -> str:
    lines = ["🌱 <b>Команды марафона</b>", "<i>сортировка по баллам</i>", ""]
    if not teams:
        lines.append("Команд пока нет — их создаёт сотрудник P&amp;C.")
    for i, row in enumerate(teams, 1):
        t: Team = row["team"]
        n = len(row["members"])
        mark = " ← твоя команда" if user.team_id == t.id else ""
        lines.append(f"#{i} {e(t.emoji)} <b>{e(t.name)}</b>")
        lines.append(f"<b>{num(row['points'])}</b> б. · {n} из {settings.team_size} участников{mark}")
        lines.append("")
    if user.team_id:
        lines.append(voice("Открой команду, чтобы посмотреть состав и вклад каждого.\nСвою найдёшь по пометке."))
    else:
        lines.append(voice(
            f"Команду назначает {pc_link()} — как только назначит, откроются задания.\n"
            "Хочешь к конкретным коллегам? Нажми «Помощь» и напиши, я передам."
        ))
    return "\n".join(lines)


def team_card(team: Team, members: list[User], points: int, rank: int | None, user_pts: dict[int, int], viewer: User) -> str:
    lines = [f"{e(team.emoji)} <b>{e(team.name)}</b>", ""]
    lines.append(row("⚡", "Баллы команды", num(points), f"🏆 #{rank}" if rank else ""))
    lines.append(row("👥", "Состав", f"{len(members)} из {settings.team_size}"))
    lines.append("")
    lines.append(rule("Кто в команде"))
    for i, m in enumerate(sorted(members, key=lambda x: -user_pts.get(x.id, 0)), 1):
        cap = " 👑" if team.captain_id == m.id else ""
        you = " ← это ты" if m.id == viewer.id else ""
        lines.append(f"{i}. {e(m.display_name)}{cap} — <b>{num(user_pts.get(m.id, 0))}</b> б.{you}")
    free = settings.team_size - len(members)
    lines.append("")
    lines.append(f"<i>Свободных мест: {free} из {settings.team_size}</i>" if free > 0
                 else "<i>Команда в полном составе</i>")
    lines.append("")
    mine = viewer.team_id == team.id
    my_pts = user_pts.get(viewer.id, 0)
    if not mine:
        line = "Это чужая команда — посмотреть можно, а состав меняет только P&amp;C."
    elif rank == 1:
        line = f"Ваша команда впереди. Каждое новое дело закрепляет отрыв {plain('rocket')}"
    elif my_pts == 0:
        line = "Твой вклад пока нулевой — одно дело на этой неделе, и ты в таблице."
    else:
        line = f"Твои <b>{num(my_pts)}</b> б. уже в общем счёте. Возьми ещё дело — команда подтянется."
    lines.append(voice(line))
    return "\n".join(lines)


def leaderboard_text(rows: list[dict]) -> str:
    lines = ["🏆 <b>Рейтинг команд</b>", ""]
    medals = [px("medal"), "🥈", "🥉"]
    if not rows:
        lines.append("Счёт пока не открыт — всё впереди.")
    for i, r in enumerate(rows):
        t: Team = r["team"]
        mark = medals[i] if i < 3 else f"#{i + 1}"
        members = f" · {len(r['members'])} чел." if r.get("members") else ""
        lines.append(f"{mark} {e(t.emoji)} <b>{e(t.name)}</b> — <b>{num(r['points'])}</b> б.{members}")
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


def week_period(week: int) -> str:
    """«с 9 по 15 сентября» — период недели человеческим языком."""
    w = next((x for x in settings.weeks if x.number == week), None)
    if not w:
        return ""
    if w.start.month == w.end.month:
        return f"с {w.start.day} по {_date_ru(w.end)}"
    return f"с {_date_ru(w.start)} по {_date_ru(w.end)}"


def week_max_points(tasks: list[Task]) -> int:
    """Всё, что можно набрать за неделю: у задания с вариантами берём самый дорогой."""
    total = 0
    for t in tasks:
        total += max((o.points for o in t.options), default=t.points)
    return total


def tasks_soon() -> str:
    """Ни одна неделя не открыта: участник должен понимать, что всё идёт по плану."""
    return (
        "📅 <b>Задания скоро откроются</b>\n"
        "<i>ждём старта недели</i>\n\n"
        "Каждую неделю появляются <b>4 задания</b>, от <b>200</b> до <b>500</b> баллов за каждое. "
        "Как только неделя откроется, я пришлю сюда список — пропустить не получится.\n\n"
        + voice("Пока можно заглянуть в «Правила» — там сроки, условия зачёта и как всё считается.\n"
                "Подготовься заранее, и первое дело займёт десять минут.")
    )


def tasks_list(week: int, tasks: list[Task], subs: dict[int, Submission]) -> str:
    from . import services

    is_now = services.week_is_open(week)
    is_past = not is_now

    lines = [f"📅 <b>Неделя {week}</b>", f"<i>Период проведения: {week_period(week)}</i>"]
    if is_past:
        lines[1] += " · <i>неделя закрыта</i>"
    lines.append("")

    for i, task in enumerate(tasks, 1):
        sub = subs.get(task.id)
        mark = ""
        if sub and sub.status != SubmissionStatus.cancelled:
            mark = f" · {STATUS_ICON[sub.status]} {STATUS_LABEL[sub.status]}"
        lines.append(f"<b>Задание {i}</b>")
        lines.append(f"{e(task.emoji)} {e(task.title)}")
        lines.append(f"<b>{task.points_label}</b> {plural(max(task.points, 1), 'балл', 'балла', 'баллов')}{mark}")
        lines.append("")

    done = len([x for x in subs.values() if x.week == week and x.status in (SubmissionStatus.pending, SubmissionStatus.approved)])
    total = week_max_points(tasks)
    hint = "\nНажми на задание — расскажу условия и приму отчёт."
    if not is_now:
        line = f"Неделя уже закрыта, но условия можно посмотреть.{hint}"
    elif done == 0:
        line = (f"Друзья, выполнив все задания недели, можно заработать <b>{num(total)}</b> "
                f"{plural(total, 'балл', 'балла', 'баллов')}. Возьми хотя бы одно — этого уже "
                f"достаточно, чтобы неделя засчиталась.{hint}")
    elif done >= 4:
        line = f"Четыре из четырёх — максимум за неделю. Снимаю шляпу {plain('cool')}{hint}"
    else:
        left = 4 - done
        line = (f"Уже {done} из 4. Можно взять ещё {left} {plural(left, 'задание', 'задания', 'заданий')} "
                f"и добрать до <b>{num(total)}</b> {plural(total, 'балла', 'баллов', 'баллов')} за неделю.{hint}")
    lines.append(voice(line))
    return "\n".join(lines)


def task_card(task: Task, sub: Submission | None, number: int | None = None) -> str:
    head = f"Задание {number}" if number else f"Неделя {task.week}"
    lines = [f"<b>{head}</b>", f"{e(task.emoji)} <b>{e(task.title)}</b>"]
    lines.append(f"<b>{task.points_label}</b> {plural(max(task.points, 1), 'балл', 'балла', 'баллов')} "
                 f"· принимаем {week_period(task.week)}")
    lines.append("")
    lines.append(e(task.description))
    lines.append("")
    if task.options:
        # Full checklists live in the report screen; the card stays short enough for a photo caption.
        lines.append(rule("Один вариант на выбор — оба сделать нельзя"))
        for o in task.options:
            lines.append(f"· {e(o.title)} — <b>{o.points}</b> б.")
        lines.append("")
        lines.append("<i>Что приложить — покажу, когда выберешь вариант.</i>")
    else:
        lines.append(rule("Что приложить к отчёту"))
        lines.append(quote(e(task.conditions), expandable=True))

    if sub and sub.status != SubmissionStatus.cancelled:
        lines.append("")
        opt = f" · {e(sub.option.title)}" if sub.option else ""
        lines.append(f"{STATUS_ICON[sub.status]} <b>{STATUS_LABEL[sub.status].capitalize()}</b>{opt}")
        if sub.status == SubmissionStatus.approved:
            lines.append(voice(f"Зачтено, <b>{sub.points_awarded}</b> б. ушли команде. Спасибо {plain('heart')}"))
        elif sub.status == SubmissionStatus.rejected and sub.review_comment:
            lines.append(f"Причина: <i>{e(sub.review_comment)}</i>")
            lines.append(voice("Это поправимо — доснимай, что просят, и отправляй снова.\nЖми «Сделать и отправить отчёт»."))
        elif sub.status == SubmissionStatus.pending:
            lines.append(voice("Отчёт у сотрудника P&amp;C. Как проверят — сразу напишу сюда."))
        return "\n".join(lines)

    lines.append("")
    if task.options:
        lines.append(voice("Выбери вариант — покажу, что приложить, и приму отчёт."))
    else:
        lines.append(voice("Сделал? Жми «Сделать и отправить отчёт» — приложишь фото и пару строк."))
    return "\n".join(lines)


KIND_WORD = {
    "photo": ("📷", "Пришлите фото", "фото"),
    "file": ("📎", "Пришлите файл", "файл"),
    "note": ("✍️", "Напишите сообщением", "текст"),
}


def _steps_progress(steps: list[dict], done: list[bool], current: int | None = None) -> list[str]:
    """Полоса шагов: что уже принято, что идёт сейчас, что впереди."""
    lines = []
    for i, st in enumerate(steps):
        if done[i]:
            mark = "✅"
        elif i == current:
            mark = "▶️"
        else:
            mark = "⏳"
        title = f"<b>{e(st['title'])}</b>" if i == current else e(st["title"])
        lines.append(f"{mark} Шаг {i + 1}. {title}")
    return lines


def submission_step(sub: Submission, index: int, warning: str = "") -> str:
    """Экран одного шага: что именно приложить и как это сделать."""
    from . import services

    steps = services.submission_steps(sub)
    done = [services.step_done(sub, i) for i in range(len(steps))]
    st = steps[index]
    icon, action, what = KIND_WORD.get(st.get("kind", "photo"), KIND_WORD["photo"])

    lines = [f"<b>Шаг {index + 1} из {len(steps)}</b>", f"{icon} <b>{e(st['title'])}</b>"]
    if sub.option:
        lines.append(f"<i>{e(sub.task.title)} · {e(sub.option.title)}</i>")
    else:
        lines.append(f"<i>{e(sub.task.title)}</i>")
    lines.append("")
    lines.append(e(st.get("need", "")))
    lines.append("")
    lines.append(f"{action} прямо в этот чат — я подхвачу и открою следующий шаг.")
    if warning:
        lines.append("")
        lines.append(f"❗ {e(warning)}")
    lines.append("")
    lines.extend(_steps_progress(steps, done, index))
    lines.append("")
    help_text = st.get("help", "")
    left = len([i for i in range(len(steps)) if not done[i] and i != index])
    tail = ("Это последний шаг — дальше покажу всё вместе и отправим на проверку."
            if left == 0 else f"После этого останется ещё {left} "
            f"{plural(left, 'шаг', 'шага', 'шагов')}. Идём по одному, спешить некуда.")
    lines.append(voice((help_text + "\n" if help_text else "") + tail))
    return "\n".join(lines)


def submission_review(sub: Submission) -> str:
    """Итог перед отправкой: что собрано на каждом шаге."""
    from . import services

    steps = services.submission_steps(sub)
    answers = sub.answers or {}
    lines = ["<b>Отчёт готов</b>", f"{e(sub.task.emoji)} <b>{e(sub.task.title)}</b>"]
    if sub.option:
        lines.append(f"<i>вариант: {e(sub.option.title)} — {sub.option.points} б.</i>")
    lines.append("")
    for i, st in enumerate(steps):
        ok = services.step_done(sub, i)
        lines.append(f"{'✅' if ok else '⏳'} <b>Шаг {i + 1}. {e(st['title'])}</b>")
        if st.get("kind") == "note":
            lines.append(f"<i>{e(answers.get(str(i), 'пока пусто'))}</i>")
        else:
            n = len([f for f in (sub.files or []) if f.get("step") == i])
            lines.append(f"<i>{'приложено: ' + str(n) if n else 'пока пусто'}</i>")
        lines.append("")

    missing = services.steps_left(sub)
    if missing:
        nums = ", ".join(str(i + 1) for i in missing)
        lines.append(voice(f"Не хватает шагов: {nums}. Нажми на нужный шаг ниже и дошли, чего не хватает."))
    else:
        lines.append(voice(f"Всё на месте. Жми «Отправить на проверку» — дальше смотрит P&amp;C {plain('rocket')}\n"
                           "Если что-то захочешь переснять, шаг можно открыть заново."))
    return "\n".join(lines)


def submission_editor(sub: Submission) -> str:
    """Старый вид отчёта — для заданий, у которых ещё не описаны шаги."""
    task = sub.task
    files = len(sub.files or [])
    need = sub.required_photos

    lines = ["<b>Отчёт по заданию</b>", f"{e(task.emoji)} <b>{e(task.title)}</b>"]
    if sub.option:
        lines.append(f"<i>вариант: {e(sub.option.title)} — {sub.option.points} б.</i>")
    lines.append("")
    lines.append(rule("Что приложить"))
    lines.append(quote(e(sub.option.conditions if sub.option else task.conditions), expandable=True))
    lines.append("")

    ok_f = "✅" if files >= need else "⏳"
    if files >= need:
        lines.append(f"{ok_f} Фото и файлы: <b>{files}</b> — достаточно")
    else:
        lines.append(f"{ok_f} Фото и файлы: <b>{files} из {need}</b> — просто пришли их сюда")

    note_mark = "✅" if sub.note else ("⏳" if task.note_required else "➖")
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
        lines.append(voice("Осталось добавить " + " и ".join(missing) + ".\nПришли прямо сюда — я всё соберу."))
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
    lines = ["⚡ <b>Мой вклад</b>", ""]
    lines.append(row("⭐", "Баллы", num(total)))
    lines.append(row("✅", "Зачтено дел", str(done)))
    lines.append("")
    for w in settings.weeks:
        ws = [x for x in subs if x.week == w.number and x.status != SubmissionStatus.cancelled]
        lines.append(rule(f"Неделя {w.number}"))
        if not ws:
            lines.append("<i>пока пусто</i>")
        for x in ws:
            pts = f" · <b>+{x.points_awarded}</b>" if x.status == SubmissionStatus.approved else ""
            lines.append(f"{STATUS_ICON[x.status]} {e(x.task.title)}{pts}")
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
    lines = [intro, f"<i>Период проведения: {week_period(week)}</i>", ""]
    for i, task in enumerate(tasks, 1):
        lines.append(f"<b>Задание {i}</b>")
        lines.append(f"{e(task.emoji)} {e(task.title)}")
        lines.append(f"<b>{task.points_label}</b> {plural(max(task.points, 1), 'балл', 'балла', 'баллов')}")
        lines.append("")

    total = week_max_points(tasks)
    closing = {
        1: "Начни с любого — важно просто начать.",
        2: "Первая неделя позади. Вторая обычно даётся легче.",
        3: "Последний рывок. Ещё есть время подтянуть команду в таблице.",
    }.get(week, "Открывай «Задания недели» и выбирай.")
    lines.append(voice(
        f"Друзья, выполнив все задания недели, можно заработать <b>{num(total)}</b> "
        f"{plural(total, 'балл', 'балла', 'баллов')}. {closing}\n"
        f"Успеть нужно до {_date_ru(settings.week_deadline(week).date())} — жми «Задания недели»."
    ))
    return "\n".join(lines)


def week_reminder(week: int) -> str:
    return (
        f"⏰ <b>Неделя {week} заканчивается завтра</b>\n"
        f"<i>принимаем до {_date_ru(settings.week_deadline(week).date())}</i>\n\n"
        "На этой неделе у тебя пока нет ни одного отправленного дела.\n\n"
        + voice("Есть задания на десять минут — написать благодарность коллеге или поделиться "
                f"полезным материалом. Успеть реально прямо сегодня {plain('hug')}\n"
                "Жми «Задания недели» — покажу, что осталось.")
    )


# ---------- channel cards ----------

USER_STATUS_LINE = {
    UserStatus.new: "🆕 не завершил регистрацию",
    UserStatus.pending: "⏳ ожидает решения P&amp;C",
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
        who = f" (P&amp;C id {user.moderated_by})" if user.moderated_by else ""
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
        lines.append(f"✅ <b>Зачтено{when}</b> — начислено {sub.points_awarded} б. (P&amp;C id {sub.reviewed_by})")
    elif sub.status == SubmissionStatus.rejected:
        when = f" · {sub.reviewed_at.strftime('%d.%m %H:%M')}" if sub.reviewed_at else ""
        lines.append(f"❌ <b>Отклонено{when}</b> (P&amp;C id {sub.reviewed_by})")
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
            + voice(f"Заявку посмотрит {pc_link()} и подтвердит участие. "
                    "Как только это случится — напишу сюда, и откроется меню с заданиями.")
        )
    return (
        f"{px('blocked')} <b>Заявка отклонена</b>\n\n"
        f"Причина: <i>{e(user.reject_reason or 'не указана')}</i>\n\n"
        + voice("Если это недоразумение — напиши сотруднику P&amp;C, разберёмся. "
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


def help_screen() -> str:
    return (
        "💬 <b>Помощь</b>\n"
        f"<i>на связи {pc_link()}</i>\n\n"
        "Не получается выбрать команду или есть вопрос по марафону — выбери пункт ниже, "
        "я передам сотруднику P&amp;C и вернусь с ответом сюда.\n\n"
        + voice("Ответы на большинство вопросов есть в «Правилах» — там же сроки и условия зачёта.\n"
                f"Если вопрос срочный, напиши напрямую: {pc_link()}.")
    )


def help_team_sent(delivered: int) -> str:
    """Что видит участник после запроса на распределение в команду."""
    lines = ["✅ <b>Запрос отправлен</b>", "", f"Команду подберёт {pc_link()} — как назначит, я напишу сюда."]
    if not delivered:
        lines.append("")
        lines.append("<i>Пока некому передать запрос автоматически — напиши напрямую, пожалуйста.</i>")
    lines.append("")
    lines.append(voice("Пока ждёшь — загляни в «Правила» и «Задания недели», чтобы выбрать первое дело.\n"
                       f"Есть вопрос прямо сейчас? Напиши {pc_link()}."))
    return "\n".join(lines)


def submission_sent(task: Task) -> str:
    return (
        f"{px('salute')} <b>Отчёт отправлен</b>\n"
        f"{e(task.title)}\n\n"
        + voice("Сотрудник P&amp;C проверит и начислит баллы. Как решит — сразу напишу сюда.")
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
        + voice("Сотрудник P&amp;C подберёт новую. Задания на это время поставлены на паузу.")
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
        + voice("Баллы сохранены. Сотрудник P&amp;C переведёт тебя в другую команду.")
    )


def user_delete_confirm(user: User, subs: int, points: int) -> str:
    """Экран подтверждения: показываем, что именно исчезнет."""
    return (
        "🗑 <b>Удалить участника из бота?</b>\n"
        f"<i>{e(user.display_name)}</i>\n\n"
        + row("📤", "Отчётов будет удалено", str(subs)) + "\n"
        + row("⭐", "Баллов снимется", num(points)) + "\n"
        + row("🌱", "Команда", e(user.team.name) if user.team else "—") + "\n\n"
        "Действие необратимое: анкета, отчёты и баллы исчезнут, командный счёт уменьшится.\n"
        "Участник сможет пройти регистрацию заново с нуля.\n\n"
        + voice("Если нужно только отстранить — вернись назад и выбери «Дисквалифицировать»: "
                "там данные сохраняются.")
    )


def user_deleted(info: dict) -> str:
    return (
        "🗑 <b>Участник удалён</b>\n"
        f"<i>{e(info['name'])}</i>\n\n"
        + row("📤", "Удалено отчётов", str(info["submissions"])) + "\n"
        + row("⭐", "Снято баллов", num(info["points"])) + "\n\n"
        + voice("Он может начать заново: достаточно нажать «Старт» в боте.")
    )


def push_deleted() -> str:
    """Что видит участник, которого удалили из бота."""
    return (
        "🗑 <b>Твоя анкета удалена</b>\n\n"
        "Данные, отчёты и баллы больше не хранятся в боте.\n\n"
        + voice("Если это недоразумение или хочешь участвовать снова — просто нажми «Старт», "
                f"и мы заполним анкету заново.\nВопросы — {pc_link()}.")
    )


def push_disqualified(reason: str) -> str:
    return (
        f"{px('blocked')} <b>Ты снят с марафона</b>\n"
        f"Причина: <i>{e(reason)}</i>\n\n"
        + voice("Результаты больше не идут в командный зачёт. Если это ошибка — напиши сотруднику P&amp;C.")
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
