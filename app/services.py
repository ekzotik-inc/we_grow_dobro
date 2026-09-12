"""Business logic. Every function takes an AsyncSession and never touches Telegram."""
from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from .config import settings
from .models import AppSetting, PointsLog, Submission, SubmissionStatus, Task, Team, User, UserStatus


class ServiceError(Exception):
    """User-facing error (message is shown to the user)."""


# ---------- runtime settings (editable from /admin) ----------

SETTING_DEFAULTS = {
    "reg_channel_id": lambda: settings.reg_channel_id,
    "results_channel_id": lambda: settings.results_channel_id,
    "registration_open": lambda: "1",
    "submissions_open": lambda: "1",
    "moderation_required": lambda: "1",
}

_settings_cache: dict[str, str | None] = {}


async def get_setting(s, key: str) -> str | None:
    """Runtime setting: DB value wins, otherwise the .env / built-in default."""
    if key in _settings_cache:
        return _settings_cache[key]
    row = (await s.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    value = row.value if row and row.value is not None else None
    if value is None:
        default = SETTING_DEFAULTS.get(key)
        value = default() if default else None
    _settings_cache[key] = value
    return value


async def set_setting(s, key: str, value: str | None) -> None:
    row = (await s.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    if row is None:
        row = AppSetting(key=key)
        s.add(row)
    row.value = value
    await s.flush()
    _settings_cache[key] = value if value is not None else (SETTING_DEFAULTS[key]() if key in SETTING_DEFAULTS else None)


def invalidate_settings_cache() -> None:
    _settings_cache.clear()


async def get_flag(s, key: str) -> bool:
    return str(await get_setting(s, key) or "") == "1"


async def get_channel_id(s, key: str) -> int | None:
    raw = await get_setting(s, key)
    if raw and str(raw).lstrip("-").isdigit():
        return int(raw)
    return None


# ---------- users ----------

async def get_or_create_user(s, tg_id: int, username: str | None) -> User:
    user = (await s.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if user is None:
        user = User(tg_id=tg_id, username=username, is_admin=settings.is_admin(tg_id), is_pc=settings.is_pc(tg_id))
        s.add(user)
        await s.flush()
    else:
        changed = False
        if username != user.username:
            user.username = username
            changed = True
        # Права всегда берутся из ADMIN_IDS / PC_IDS: флаг в базе только отражает список,
        # а не даёт доступ сам по себе. Иначе однажды выставленный флаг остаётся навсегда.
        # Исключение — пустой ADMIN_IDS: тогда никого не разжалуем, чтобы не остаться без панели.
        if settings.admin_ids:
            if user.is_admin != settings.is_admin(tg_id):
                user.is_admin = settings.is_admin(tg_id)
                changed = True
            if user.is_pc != settings.is_pc(tg_id):
                user.is_pc = settings.is_pc(tg_id)
                changed = True
        if changed:
            await s.flush()
    return user


async def sync_staff_flags(s) -> int:
    """Привести права в базе в соответствие с ADMIN_IDS и PC_IDS.

    Выполняется при старте: участник, которому права достались от прежних настроек, теряет их,
    даже если больше никогда не откроет бота.
    """
    changed = 0
    for user in (await s.execute(select(User))).scalars():
        want_admin = settings.is_admin(user.tg_id)
        want_pc = settings.is_pc(user.tg_id)
        if user.is_admin != want_admin or user.is_pc != want_pc:
            user.is_admin, user.is_pc = want_admin, want_pc
            changed += 1
    if changed:
        await s.flush()
    return changed


async def get_user(s, tg_id: int) -> User | None:
    return (
        await s.execute(select(User).options(selectinload(User.team)).where(User.tg_id == tg_id))
    ).scalar_one_or_none()


async def get_user_by_id(s, user_id: int) -> User | None:
    return (await s.execute(select(User).options(selectinload(User.team)).where(User.id == user_id))).scalar_one_or_none()


async def save_draft(s, user: User, full_name: str | None = None, phone: str | None = None,
                     ref_team_id: int | None = None, invited_by: int | None = None) -> None:
    """Сохранить шаг анкеты сразу в базу.

    Состояние диалога живёт в памяти процесса, а хостинг усыпляет и перезапускает сервис —
    без этого участник, приславший телефон после перезапуска, не получал никакого ответа.
    """
    if full_name is not None:
        user.full_name = full_name.strip()[:160]
    if phone is not None:
        user.phone = phone.strip()[:32] or None
    if ref_team_id is not None:
        user.ref_team_id = ref_team_id or None
    if invited_by is not None:
        user.invited_by = invited_by or None
    await s.flush()


async def draft_from_user(s, user: User) -> dict:
    """Черновик анкеты, восстановленный из базы после перезапуска бота."""
    draft = {}
    if user.full_name:
        draft["full_name"] = user.full_name
    if user.phone:
        draft["phone"] = user.phone
    if user.ref_team_id:
        # Команда уже выбрана пригласившим — анкета из двух шагов.
        team = await get_team(s, user.ref_team_id)
        if team is not None:
            draft["team_id"] = team.id
            draft["team_name"] = f"{team.emoji} {team.name}"
            draft["total"] = 2
    return draft


def draft_step(user: User) -> str:
    """На каком шаге анкеты остановился участник."""
    if not user.full_name:
        return "full_name"
    if not user.phone:
        return "phone"
    # По приглашению команда уже известна — сразу к подтверждению.
    return "confirm" if user.ref_team_id else "team"


def invite_link(bot_username: str, team_id: int, inviter_tg_id: int) -> str:
    """Ссылка-приглашение в конкретную команду: получатель регистрируется в два шага."""
    return f"https://t.me/{bot_username}?start=ref_{team_id}_{inviter_tg_id}"


def parse_invite(payload: str) -> tuple[int, int] | None:
    """Разобрать полезную нагрузку deep link «ref_<команда>_<пригласивший>»."""
    m = re.match(r"^ref_(\d+)(?:_(\d+))?$", payload.strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 0)


async def register_user(
    s, user: User, full_name: str, department: str | None, city: str | None,
    phone: str | None = None, wanted_team_id: int | None = None,
) -> UserStatus:
    """Save the form. With moderation on, the user waits for P&C approval; otherwise joins right away."""
    user.full_name = full_name.strip()[:160]
    user.department = (department or "").strip()[:160] or None
    user.city = (city or "").strip()[:80] or None
    if phone is not None:
        user.phone = phone.strip()[:32] or None
    user.wanted_team_id = wanted_team_id or user.ref_team_id
    user.rules_accepted_at = datetime.utcnow()
    user.reject_reason = None
    moderation = await get_flag(s, "moderation_required")
    user.status = UserStatus.pending if moderation else UserStatus.registered
    await s.flush()
    return user.status


async def approve_user(s, user: User, actor_tg_id: int) -> None:
    """Accept the application and, when the team they asked for still has room, put them there."""
    if user.status == UserStatus.registered:
        raise ServiceError("Участник уже принят.")
    user.status = UserStatus.registered
    user.reject_reason = None
    user.moderated_by = actor_tg_id
    user.moderated_at = datetime.utcnow()
    if not user.team_id and user.wanted_team_id:
        wanted = await get_team(s, user.wanted_team_id)
        if wanted and await team_active_count(s, wanted.id) < settings.team_size:
            user.team_id = wanted.id
    await s.flush()


async def reject_user(s, user: User, actor_tg_id: int, reason: str) -> None:
    if user.status == UserStatus.rejected:
        raise ServiceError("Заявка уже отклонена.")
    user.status = UserStatus.rejected
    user.reject_reason = reason
    user.moderated_by = actor_tg_id
    user.moderated_at = datetime.utcnow()
    await s.flush()


async def pending_users(s) -> list[User]:
    return list(
        (
            await s.execute(
                select(User).options(selectinload(User.team)).where(User.status == UserStatus.pending).order_by(User.created_at)
            )
        ).scalars()
    )


async def pending_users_count(s) -> int:
    return (await s.execute(select(func.count()).select_from(User).where(User.status == UserStatus.pending))).scalar_one()


async def list_participants(s) -> list[User]:
    return list(
        (
            await s.execute(
                select(User)
                .options(selectinload(User.team))
                .where(User.status != UserStatus.new)
                .order_by(User.full_name)
            )
        ).scalars()
    )


async def list_admin_tg_ids(s) -> set[int]:
    """Everyone with panel access: the owner plus P&C staff."""
    ids = set(settings.admin_ids) | set(settings.pc_ids)
    rows = (await s.execute(select(User.tg_id).where(User.is_admin.is_(True) | User.is_pc.is_(True)))).scalars()
    ids.update(rows)
    return ids


async def list_pc_tg_ids(s) -> set[int]:
    """Only P&C staff — participants' questions and team requests must not reach the owner."""
    ids = set(settings.pc_ids)
    rows = (await s.execute(select(User.tg_id).where(User.is_pc.is_(True)))).scalars()
    ids.update(rows)
    return ids or await list_admin_tg_ids(s)  # nobody marked as P&C yet: fall back to the panel


async def delete_user(s, user: User) -> dict:
    """Полностью убрать участника из бота, чтобы он мог зарегистрироваться заново.

    Удаляются отчёты и записи о баллах — в командном зачёте его результатов больше нет.
    Возвращает, что именно было удалено: это показывается админу перед подтверждением
    и в отчёте о выполнении.
    """
    subs = list((await s.execute(select(Submission).where(Submission.user_id == user.id))).scalars())
    points = await user_points(s, user.id)
    tg_id, name = user.tg_id, user.display_name

    # Капитанство снимаем вручную: ссылка на users.id живёт без внешнего ключа.
    for team in (await s.execute(select(Team).where(Team.captain_id == user.id))).scalars():
        team.captain_id = None
    await s.execute(delete(PointsLog).where(PointsLog.user_id == user.id))
    await s.execute(delete(Submission).where(Submission.user_id == user.id))
    await s.delete(user)
    await s.flush()
    return {"tg_id": tg_id, "name": name, "submissions": len(subs), "points": points}


# ---------- ручная корректировка результатов (/addresult, только владелец) ----------

async def adjust_points(s, user: User, delta: int, reason: str, actor_tg_id: int) -> int:
    """Начислить или списать баллы вручную. Возвращает новый итог участника."""
    if delta == 0:
        raise ServiceError("Нечего менять: укажите число, отличное от нуля.")
    user.manual_points = int(user.manual_points or 0) + delta
    s.add(PointsLog(user_id=user.id, submission_id=None, delta=delta,
                    reason=reason[:200] or "ручная корректировка", actor_tg_id=actor_tg_id))
    await s.flush()
    return await user_points(s, user.id)


async def revoke_review(s, sub: Submission, actor_tg_id: int, reason: str) -> int:
    """Отменить зачёт отчёта: баллы снимаются, участник может переделать работу."""
    if sub.status != SubmissionStatus.approved:
        raise ServiceError("Этот отчёт не был зачтён.")
    returned = sub.points_awarded
    s.add(PointsLog(user_id=sub.user_id, submission_id=sub.id, delta=-returned,
                    reason=f"отмена зачёта: {reason}"[:200], actor_tg_id=actor_tg_id))
    sub.status = SubmissionStatus.rejected
    sub.points_awarded = 0
    sub.review_comment = reason[:300]
    sub.reviewed_by = actor_tg_id
    sub.reviewed_at = datetime.utcnow()
    await s.flush()
    return returned


async def clear_user_results(s, user: User, actor_tg_id: int) -> dict:
    """Полностью обнулить результаты участника: отчёты, баллы и ручные корректировки."""
    subs = list((await s.execute(select(Submission).where(Submission.user_id == user.id))).scalars())
    before = await user_points(s, user.id)
    await s.execute(delete(PointsLog).where(PointsLog.user_id == user.id))
    await s.execute(delete(Submission).where(Submission.user_id == user.id))
    user.manual_points = 0
    s.add(PointsLog(user_id=user.id, submission_id=None, delta=-before,
                    reason="обнуление результатов", actor_tg_id=actor_tg_id))
    await s.flush()
    return {"submissions": len(subs), "points": before}


async def clear_team_results(s, team: Team, actor_tg_id: int) -> dict:
    """Обнулить результаты всей команды — каждому участнику по отдельности.

    Состав берём запросом, а не через связь: после первых удалений связь устаревает,
    и обращение к ней падает посреди операции.
    """
    members = list(
        (await s.execute(
            select(User).where(User.team_id == team.id, User.status != UserStatus.disqualified)
        )).scalars()
    )
    total = {"members": 0, "submissions": 0, "points": 0}
    for member in members:
        info = await clear_user_results(s, member, actor_tg_id)
        total["members"] += 1
        total["submissions"] += info["submissions"]
        total["points"] += info["points"]
    await s.flush()
    return total


async def recent_points_log(s, limit: int = 15) -> list[tuple[PointsLog, User | None]]:
    """Последние начисления и списания — журнал для владельца."""
    rows = (
        await s.execute(select(PointsLog).order_by(PointsLog.id.desc()).limit(limit))
    ).scalars()
    out = []
    for entry in rows:
        user = (await s.execute(select(User).where(User.id == entry.user_id))).scalar_one_or_none()
        out.append((entry, user))
    return out


async def disqualify(s, user: User, reason: str, actor_tg_id: int) -> None:
    user.status = UserStatus.disqualified
    user.disqualified_reason = reason
    # Points are excluded from team totals by status; log it for audit.
    total = await user_points(s, user.id)
    if total:
        s.add(PointsLog(user_id=user.id, delta=-total, reason=f"Дисквалификация: {reason}", actor_tg_id=actor_tg_id))
    await s.flush()


async def reinstate(s, user: User, actor_tg_id: int) -> None:
    user.status = UserStatus.registered
    user.disqualified_reason = None
    total = await user_points(s, user.id)
    if total:
        s.add(PointsLog(user_id=user.id, delta=total, reason="Восстановление участника", actor_tg_id=actor_tg_id))
    await s.flush()


# ---------- teams ----------

async def list_teams(s) -> list[Team]:
    return list((await s.execute(select(Team).options(selectinload(Team.members)).order_by(Team.name))).scalars())


async def get_team(s, team_id: int) -> Team | None:
    return (await s.execute(select(Team).options(selectinload(Team.members)).where(Team.id == team_id))).scalar_one_or_none()


def team_active_members(team: Team) -> list[User]:
    return [m for m in team.members if m.status != UserStatus.disqualified]


async def team_active_count(s, team_id: int) -> int:
    """Fresh count from the DB (relationship collections may be stale inside a long session)."""
    return (
        await s.execute(
            select(func.count()).select_from(User).where(User.team_id == team_id, User.status != UserStatus.disqualified)
        )
    ).scalar_one()


def ensure_approved(user: User) -> None:
    """Raise a user-facing error unless the participant passed P&C moderation."""
    if user.status == UserStatus.pending:
        raise ServiceError("Ваша заявка ещё на модерации у сотрудника P&C. Дождитесь подтверждения.")
    if user.status == UserStatus.rejected:
        raise ServiceError("Ваша заявка отклонена. Обратитесь к сотруднику P&C.")
    if user.status == UserStatus.disqualified:
        raise ServiceError("Вы дисквалифицированы с марафона.")
    if user.status != UserStatus.registered:
        raise ServiceError("Сначала пройдите регистрацию.")


async def create_team(s, user: User | None, name: str, emoji: str = "🌱") -> Team:
    """Only P&C creates teams — participants pick from the list, they never make their own."""
    name = " ".join(name.split())[:80]
    if len(name) < 2:
        raise ServiceError("Название команды слишком короткое.")
    # SQLite's lower() is ASCII-only, so compare case-insensitively in Python (teams are few).
    names = (await s.execute(select(Team.name))).scalars()
    if any(n.casefold() == name.casefold() for n in names):
        raise ServiceError("Команда с таким названием уже есть. Выберите другое название.")
    team = Team(name=name, emoji=emoji, captain_id=None)
    s.add(team)
    await s.flush()
    return team


async def join_team(s, user: User, team_id: int) -> Team:
    ensure_approved(user)
    if user.team_id:
        raise ServiceError("Вы уже состоите в команде. Сначала покиньте её.")
    team = await get_team(s, team_id)
    if team is None:
        raise ServiceError("Команда не найдена.")
    if await team_active_count(s, team.id) >= settings.team_size:
        raise ServiceError(f"В команде уже {settings.team_size} участников — она заполнена.")
    user.team_id = team.id
    await s.flush()
    return team


async def leave_team(s, user: User) -> None:
    if not user.team_id:
        raise ServiceError("Вы не состоите в команде.")
    if open_weeks():
        raise ServiceError("Марафон уже начался — смена команды возможна только через сотрудника P&C.")
    team = await get_team(s, user.team_id)
    user.team_id = None
    await s.flush()
    if team is None:
        return
    remaining = [m for m in team.members if m.id != user.id]
    if not remaining:
        await s.delete(team)  # last member left — remove the empty team
    elif team.captain_id == user.id:
        team.captain_id = remaining[0].id
    await s.flush()


async def move_user_to_team(s, user: User, team_id: int | None) -> None:
    """P&C manual assignment (bypasses lock, respects team size)."""
    if team_id is not None:
        team = await get_team(s, team_id)
        if team is None:
            raise ServiceError("Команда не найдена.")
        if user.team_id != team_id and await team_active_count(s, team_id) >= settings.team_size:
            raise ServiceError("Команда заполнена.")
    user.team_id = team_id
    await s.flush()


# ---------- tasks ----------

async def list_tasks(s, week: int | None = None) -> list[Task]:
    q = select(Task).options(selectinload(Task.options)).where(Task.is_active.is_(True)).order_by(Task.week, Task.code)
    if week:
        q = q.where(Task.week == week)
    return list((await s.execute(q)).scalars())


async def list_all_tasks(s) -> list[Task]:
    """Every task including deactivated ones (admin view)."""
    return list(
        (await s.execute(select(Task).options(selectinload(Task.options)).order_by(Task.week, Task.code))).scalars()
    )


async def get_task(s, task_id: int) -> Task | None:
    return (await s.execute(select(Task).options(selectinload(Task.options)).where(Task.id == task_id))).scalar_one_or_none()


# ---------- какие недели открыты (переключает админ в панели) ----------
# Раньше неделя открывалась по датам, и участник не понимал, почему заданий нет.
# Теперь всё решает выключатель в админ-панели: неделя включена — её задания видны и
# принимаются, выключена — их нет в списке. Значение хранится в базе, в памяти держим
# копию, чтобы синхронный код (клавиатуры, тексты) не ходил в базу на каждой строке.
_WEEK_KEY = "week_open:{}"
_open_weeks: set[int] = set()


async def load_open_weeks(s) -> set[int]:
    """Прочитать выключатели из базы в кеш процесса. Вызывается при старте бота."""
    global _open_weeks
    found = set()
    for w in settings.weeks:
        if await get_setting(s, _WEEK_KEY.format(w.number)) == "1":
            found.add(w.number)
    _open_weeks = found
    return found


async def set_week_open(s, week: int, is_open: bool) -> None:
    await set_setting(s, _WEEK_KEY.format(week), "1" if is_open else "0")
    if is_open:
        _open_weeks.add(week)
    else:
        _open_weeks.discard(week)


def open_weeks() -> list[int]:
    """Номера включённых недель по возрастанию."""
    return sorted(_open_weeks)


def week_is_open(week: int) -> bool:
    return week in _open_weeks


def current_week():
    """Неделя, которую показываем как текущую, — последняя включённая."""
    numbers = open_weeks()
    return settings.week(numbers[-1]) if numbers else None


def task_is_open(task: Task) -> bool:
    return task.is_active and week_is_open(task.week)


# Прежние имена оставлены: ими пользуются клавиатуры и мини-проверки.
def week_is_visible(week: int) -> bool:
    return week_is_open(week)


def visible_weeks() -> list[int]:
    return open_weeks()


# ---------- пошаговый отчёт ----------

def submission_steps(sub: Submission) -> list[dict]:
    """Шаги отчёта: у задания с вариантами — шаги выбранного варианта."""
    if sub.option is not None and sub.option.steps:
        return list(sub.option.steps)
    return list(sub.task.steps or [])


# Что принимается на шаге: «фото» — только фотография, «файл» — только документ.
STEP_ACCEPTS = {
    "photo": ("photo",),
    "file": ("document",),
    "note": (),
}
MIN_ANSWER_LEN = 3


def step_kind(steps: list[dict], index: int) -> str:
    return steps[index].get("kind", "photo") if 0 <= index < len(steps) else "photo"


def answer_is_valid(text: str | None) -> bool:
    """Ответ засчитывается, только если в нём есть буквы или цифры и он не короче трёх знаков."""
    body = (text or "").strip()
    if len(body) < MIN_ANSWER_LEN:
        return False
    return any(ch.isalnum() for ch in body)


def file_fits_step(file: dict, kind: str) -> bool:
    accepted = STEP_ACCEPTS.get(kind, ("photo",))
    return bool(accepted) and file.get("type") in accepted


def step_done(sub: Submission, index: int) -> bool:
    steps = submission_steps(sub)
    if index >= len(steps):
        return False
    kind = step_kind(steps, index)
    if kind == "note":
        return answer_is_valid((sub.answers or {}).get(str(index)))
    # Файл засчитывается только того типа, который просит шаг: фото вместо документа
    # (и наоборот) условие зачёта не выполняет.
    return any(f.get("step") == index and file_fits_step(f, kind) for f in (sub.files or []))


def steps_left(sub: Submission) -> list[int]:
    """Номера незаполненных обязательных шагов."""
    return [i for i, st in enumerate(submission_steps(sub))
            if not st.get("optional") and not step_done(sub, i)]


def first_unfinished_step(sub: Submission) -> int:
    """Куда вести участника: первый незаполненный шаг, иначе — экран проверки."""
    steps = submission_steps(sub)
    for i in range(len(steps)):
        if not step_done(sub, i):
            return i
    return len(steps)


def _sync_note(sub: Submission) -> None:
    """Собрать ответы текстовых шагов в одну заметку — её видят P&C в карточке отчёта."""
    steps = submission_steps(sub)
    answers = sub.answers or {}
    parts = []
    for i, st in enumerate(steps):
        if st.get("kind") == "note" and answers.get(str(i)):
            parts.append(f"{st['title']}: {answers[str(i)]}")
    if parts:
        sub.note = "\n".join(parts)


async def save_step_answer(s, sub: Submission, index: int, text: str) -> None:
    steps = submission_steps(sub)
    if steps and step_kind(steps, index) != "note":
        raise ServiceError("На этом шаге нужен файл, а не текст.")
    if not answer_is_valid(text):
        raise ServiceError("Слишком коротко. Напишите ответ словами — хотя бы несколько символов.")
    answers = dict(sub.answers or {})
    answers[str(index)] = text.strip()[:1000]
    sub.answers = answers
    _sync_note(sub)
    sub.step = first_unfinished_step(sub)
    await s.flush()


async def clear_step(s, sub: Submission, index: int) -> None:
    """Переделать шаг: убрать то, что было приложено на нём."""
    answers = dict(sub.answers or {})
    answers.pop(str(index), None)
    sub.answers = answers
    sub.files = [f for f in (sub.files or []) if f.get("step") != index]
    _sync_note(sub)
    sub.step = index
    await s.flush()


# ---------- submissions ----------

def _sub_query():
    # populate_existing: re-read relationships (option/user/team) even if the object is already in the identity map.
    return (
        select(Submission)
        .options(
            selectinload(Submission.task).selectinload(Task.options),
            selectinload(Submission.option),
            selectinload(Submission.user).selectinload(User.team),
        )
        .execution_options(populate_existing=True)
    )


async def get_submission(s, sub_id: int) -> Submission | None:
    return (await s.execute(_sub_query().where(Submission.id == sub_id))).scalar_one_or_none()


async def user_submission_for_task(s, user_id: int, task_id: int) -> Submission | None:
    return (
        await s.execute(_sub_query().where(Submission.user_id == user_id, Submission.task_id == task_id))
    ).scalar_one_or_none()


async def draft_submission(s, user_id: int) -> Submission | None:
    """Незаконченный отчёт участника.

    Нужен, чтобы продолжить сдачу после перезапуска бота: состояние диалога живёт в памяти,
    а черновик — в базе, и присланное фото не должно теряться.
    """
    return (
        await s.execute(
            select(Submission)
            .options(selectinload(Submission.task).selectinload(Task.options), selectinload(Submission.option))
            .where(Submission.user_id == user_id, Submission.status == SubmissionStatus.draft)
            .order_by(Submission.updated_at.desc())
        )
    ).scalars().first()


async def user_submissions(s, user_id: int) -> list[Submission]:
    return list((await s.execute(_sub_query().where(Submission.user_id == user_id).order_by(Submission.week, Submission.task_id))).scalars())


async def start_submission(s, user: User, task: Task, option_id: int | None) -> Submission:
    ensure_approved(user)
    if not await get_flag(s, "submissions_open"):
        raise ServiceError("Приём отчётов сейчас закрыт. Как только откроем — сообщу.")
    # Команда для отчёта не нужна: баллы записываются на участника и попадают в командный
    # зачёт автоматически, как только P&C определит его в команду.
    if not task_is_open(task):
        raise ServiceError("Это задание сейчас закрыто. Как только неделя откроется, я напишу.")
    if task.has_options and option_id is None:
        raise ServiceError("Выберите одну опцию задания.")
    option = None
    if option_id is not None:
        option = next((o for o in task.options if o.id == option_id), None)
        if option is None:
            raise ServiceError("Опция не найдена.")
    sub = await user_submission_for_task(s, user.id, task.id)
    if sub and sub.status in (SubmissionStatus.pending, SubmissionStatus.approved):
        raise ServiceError("Отчёт по этому заданию уже отправлен.")
    if sub is None:
        sub = Submission(user_id=user.id, task_id=task.id, week=task.week)
        s.add(sub)
    # rejected / cancelled / draft -> начинаем с чистого листа
    sub.option_id = option.id if option else None
    reset_submission(sub)
    await s.flush()
    return await get_submission(s, sub.id)


def reset_submission(sub: Submission) -> None:
    """Обнулить отчёт перед новой попыткой.

    Стирается всё, что осталось от прошлой сдачи. Пропустить хотя бы одно поле нельзя:
    оставшиеся `answers` делают текстовые шаги «уже выполненными», а несброшенный `step`
    уводит участника сразу на экран отправки — и отчёт уходил на проверку со старыми
    ответами, хотя человек ничего не переснял.
    """
    sub.status = SubmissionStatus.draft
    sub.note = None
    sub.files = []
    sub.answers = {}
    sub.step = 0
    sub.points_awarded = 0
    sub.review_comment = None
    sub.reviewed_by = None
    sub.reviewed_at = None
    sub.submitted_at = None
    # Карточка прошлой попытки остаётся в канале как есть: новая попытка публикуется
    # отдельной карточкой, иначе бот отредактировал бы уже закрытое решение P&C.
    sub.channel_message_id = None
    sub.channel_media_ids = []


async def add_file(s, sub: Submission, file: dict, step: int | None = None) -> int:
    files = list(sub.files or [])
    if len(files) >= 10:
        raise ServiceError("Максимум 10 файлов в одном отчёте.")
    steps = submission_steps(sub)
    if step is not None and steps:
        kind = step_kind(steps, step)
        if kind == "note":
            raise ServiceError("На этом шаге нужен текст сообщением, а не файл.")
        if not file_fits_step(file, kind):
            raise ServiceError("Нужен документ файлом, а не фотография."
                               if kind == "file" else "Нужна фотография, а не файл или видео.")
    if step is not None:
        file = dict(file, step=step)
    files.append(file)
    sub.files = files
    if step is not None:
        sub.step = first_unfinished_step(sub)
    await s.flush()
    return len(files)


async def set_note(s, sub: Submission, note: str) -> None:
    sub.note = note.strip()[:2000]
    await s.flush()


def submission_missing(sub: Submission) -> list[str]:
    """Чего не хватает для отправки. При пошаговой инструкции проверяем шаги поимённо."""
    steps = submission_steps(sub)
    if steps:
        what = {"note": "нужен текст", "file": "нужен файл", "photo": "нужно фото"}
        return [f"шаг {i + 1} ({what.get(step_kind(steps, i), 'нужно фото')}): "
                f"{steps[i]['title'].lower()}" for i in steps_left(sub)]
    missing = []
    photos = len(sub.files or [])
    if photos < sub.required_photos:
        missing.append(f"файлов: {photos} из {sub.required_photos}")
    if sub.task.note_required and not answer_is_valid(sub.note):
        missing.append("заметка/описание")
    return missing


async def send_for_review(s, sub: Submission) -> None:
    if sub.status != SubmissionStatus.draft:
        raise ServiceError("Отчёт уже отправлен.")
    if not task_is_open(sub.task):
        raise ServiceError("Неделя этого задания закончилась — отчёт отправить нельзя.")
    missing = submission_missing(sub)
    if missing:
        raise ServiceError("Не хватает: " + ", ".join(missing))
    sub.status = SubmissionStatus.pending
    sub.submitted_at = datetime.utcnow()
    await s.flush()


async def cancel_submission(s, sub: Submission) -> None:
    if sub.status not in (SubmissionStatus.draft, SubmissionStatus.pending):
        raise ServiceError("Этот отчёт уже проверен.")
    sub.status = SubmissionStatus.cancelled
    await s.flush()


async def pending_submissions(s) -> list[Submission]:
    return list(
        (await s.execute(_sub_query().where(Submission.status == SubmissionStatus.pending).order_by(Submission.submitted_at))).scalars()
    )


async def pending_count(s) -> int:
    return (await s.execute(select(func.count()).select_from(Submission).where(Submission.status == SubmissionStatus.pending))).scalar_one()


async def review_submission(s, sub: Submission, approve: bool, reviewer_tg_id: int, comment: str | None = None) -> None:
    if sub.status != SubmissionStatus.pending:
        raise ServiceError("Отчёт уже проверен другим сотрудником.")
    sub.reviewed_by = reviewer_tg_id
    sub.reviewed_at = datetime.utcnow()
    sub.review_comment = (comment or "").strip() or None
    if approve:
        sub.status = SubmissionStatus.approved
        sub.points_awarded = sub.target_points
        s.add(
            PointsLog(
                user_id=sub.user_id,
                submission_id=sub.id,
                delta=sub.points_awarded,
                reason=f"Задание №{sub.task.code}: {sub.task.title}",
                actor_tg_id=reviewer_tg_id,
            )
        )
    else:
        sub.status = SubmissionStatus.rejected
        sub.points_awarded = 0
    await s.flush()


# ---------- scoring ----------

async def user_points(s, user_id: int) -> int:
    """Баллы участника: зачтённые отчёты плюс ручная корректировка владельца (/addresult)."""
    earned = (
        await s.execute(
            select(func.coalesce(func.sum(Submission.points_awarded), 0)).where(
                Submission.user_id == user_id, Submission.status == SubmissionStatus.approved
            )
        )
    ).scalar_one()
    manual = (
        await s.execute(select(func.coalesce(User.manual_points, 0)).where(User.id == user_id))
    ).scalar_one_or_none() or 0
    return int(earned) + int(manual)


async def team_points_map(s) -> dict[int, int]:
    """team_id -> points (only active, non-disqualified members count)."""
    rows = await s.execute(
        select(User.team_id, func.coalesce(func.sum(Submission.points_awarded), 0))
        .join(Submission, Submission.user_id == User.id)
        .where(
            Submission.status == SubmissionStatus.approved,
            User.status == UserStatus.registered,
            User.team_id.is_not(None),
        )
        .group_by(User.team_id)
    )
    points = {tid: int(pts) for tid, pts in rows}
    # Ручные корректировки идут в командный зачёт так же, как зачтённые отчёты.
    manual = await s.execute(
        select(User.team_id, func.coalesce(func.sum(User.manual_points), 0))
        .where(User.status == UserStatus.registered, User.team_id.is_not(None), User.manual_points != 0)
        .group_by(User.team_id)
    )
    for tid, delta in manual:
        points[tid] = points.get(tid, 0) + int(delta or 0)
    return points


async def leaderboard(s) -> list[dict]:
    teams = await list_teams(s)
    pts = await team_points_map(s)
    rows = []
    for t in teams:
        members = team_active_members(t)
        rows.append({"team": t, "points": pts.get(t.id, 0), "members": members})
    rows.sort(key=lambda r: (-r["points"], r["team"].name.lower()))
    return rows


async def user_points_map(s) -> dict[int, int]:
    rows = await s.execute(
        select(Submission.user_id, func.coalesce(func.sum(Submission.points_awarded), 0))
        .where(Submission.status == SubmissionStatus.approved)
        .group_by(Submission.user_id)
    )
    points = {uid: int(p) for uid, p in rows}
    manual = await s.execute(select(User.id, User.manual_points).where(User.manual_points != 0))
    for uid, delta in manual:
        points[uid] = points.get(uid, 0) + int(delta or 0)
    return points


async def week_stats_for_user(s, user_id: int, week: int) -> dict:
    subs = [x for x in await user_submissions(s, user_id) if x.week == week]
    return {
        "submitted": len([x for x in subs if x.status in (SubmissionStatus.pending, SubmissionStatus.approved)]),
        "approved": len([x for x in subs if x.status == SubmissionStatus.approved]),
        "pending": len([x for x in subs if x.status == SubmissionStatus.pending]),
    }


async def users_without_submissions(s, week: int) -> list[User]:
    """Registered participants who have no pending/approved submission this week (for reminders)."""
    users = await list_participants(s)
    sub_users = set(
        (
            await s.execute(
                select(Submission.user_id).where(
                    Submission.week == week, Submission.status.in_([SubmissionStatus.pending, SubmissionStatus.approved])
                )
            )
        ).scalars()
    )
    return [u for u in users if u.status == UserStatus.registered and u.id not in sub_users]


# ---------- broadcast segments ----------

SEGMENTS: list[tuple[str, str, str]] = [
    ("all", "Все участники", "Все принятые участники марафона"),
    ("no_team", "Без команды", "Приняты, но не выбрали команду"),
    ("no_reports_week", "Нет отчётов на этой неделе", "Ни одного отправленного отчёта в текущую неделю"),
    ("lt_n_week", "Меньше N заданий за неделю", "Отправили меньше выбранного числа заданий"),
    ("no_approved_all", "Ни одного зачтённого задания", "За весь марафон нет зачтённых отчётов"),
    ("rejected_week", "Отчёт отклонён и не переделан", "Есть отклонённый отчёт без повторной отправки"),
    ("pending_review", "Ждут проверки отчёта", "Есть отчёт со статусом «на проверке»"),
    ("pending_approval", "Заявки на модерации", "Заполнили анкету и ждут решения P&C"),
    ("disqualified", "Дисквалифицированные", "Исключены из командного зачёта"),
    ("team", "Конкретная команда", "Только участники выбранной команды"),
]

SEGMENT_TITLES = {code: title for code, title, _ in SEGMENTS}


def segment_label(code: str, arg: str | None = None) -> str:
    title = SEGMENT_TITLES.get(code, code)
    if code == "lt_n_week" and arg:
        return f"Меньше {arg} заданий за неделю"
    if code == "team" and arg:
        return f"Команда #{arg}"
    return title


async def segment_users(s, code: str, arg: str | None = None) -> list[User]:
    """Recipients of a broadcast segment. Everything except the two moderation segments targets
    approved participants only."""
    users = await list_participants(s)
    active = [u for u in users if u.status == UserStatus.registered]
    cw = current_week()

    if code == "pending_approval":
        return [u for u in users if u.status == UserStatus.pending]
    if code == "disqualified":
        return [u for u in users if u.status == UserStatus.disqualified]
    if code == "all":
        return active
    if code == "no_team":
        return [u for u in active if not u.team_id]
    if code == "team":
        team_id = int(arg) if arg and str(arg).isdigit() else 0
        return [u for u in active if u.team_id == team_id]

    subs_by_user: dict[int, list[Submission]] = {}
    for u in active:
        subs_by_user[u.id] = await user_submissions(s, u.id)

    if code == "no_approved_all":
        return [u for u in active if not any(x.status == SubmissionStatus.approved for x in subs_by_user[u.id])]
    if code == "pending_review":
        return [u for u in active if any(x.status == SubmissionStatus.pending for x in subs_by_user[u.id])]

    if not cw:
        return []
    week = cw.number

    def sent_this_week(u: User) -> list[Submission]:
        return [
            x
            for x in subs_by_user[u.id]
            if x.week == week and x.status in (SubmissionStatus.pending, SubmissionStatus.approved)
        ]

    if code == "no_reports_week":
        return [u for u in active if not sent_this_week(u)]
    if code == "lt_n_week":
        n = int(arg) if arg and str(arg).isdigit() else 1
        return [u for u in active if len(sent_this_week(u)) < n]
    if code == "rejected_week":
        out = []
        for u in active:
            week_subs = [x for x in subs_by_user[u.id] if x.week == week]
            if any(x.status == SubmissionStatus.rejected for x in week_subs):
                out.append(u)
        return out
    return []


async def nudge_for_user(s, user: User) -> tuple[str, dict] | None:
    """Какая подсказка нужна именно этому человеку — одна, самая полезная сейчас.

    Порядок важен: сначала то, что человек уже начал и бросил, потом то, что мешает
    начать, и только потом похвала. Так пуш всегда про его следующий шаг, а не «вообще».
    """
    if user.status != UserStatus.registered:
        return None
    cw = current_week()
    if cw is None:
        return None
    week = cw.number
    days_left = max((cw.end - settings.today()).days, 0)
    base = {"days_left": days_left, "week": week}

    subs = await user_submissions(s, user.id)
    week_subs = [x for x in subs if x.week == week]

    # 1. Брошенный черновик: человек начал и остановился на середине.
    for sub in week_subs:
        if sub.status != SubmissionStatus.draft:
            continue
        steps = submission_steps(sub)
        left = steps_left(sub)
        started = bool(sub.files or sub.answers)
        if steps and left and started:
            index = left[0]
            return "draft", {**base, "task": sub.task.title,
                             "step_title": steps[index].get("title", f"шаг {index + 1}")}

    # 2. Отклонённый отчёт, который так и не переделали.
    for sub in week_subs:
        if sub.status == SubmissionStatus.rejected:
            return "rejected", {**base, "task": sub.task.title,
                                "reason": sub.review_comment or ""}

    sent = [x for x in week_subs if x.status in (SubmissionStatus.pending, SubmissionStatus.approved)]
    tasks = await list_tasks(s, week)
    total = len(tasks)

    # 3. Ничего не сдано — подсказываем самое дешёвое (а значит, самое простое) дело.
    if not sent:
        if not user.team_id:
            return "no_team", base
        done_ids = {x.task_id for x in week_subs}
        free = [t for t in tasks if t.id not in done_ids and task_is_open(t)]
        if not free:
            return None
        easiest = min(free, key=lambda t: t.points)
        return "zero", {**base, "easiest": easiest.title, "points": easiest.points}

    # 4. Всё сдано — хвалим и не зовём никуда.
    approved = [x for x in sent if x.status == SubmissionStatus.approved]
    if len(sent) >= total:
        return "all_done", {**base, "points": sum(x.points_awarded for x in approved)}

    # 5. Первый отчёт ещё на проверке и других нет — объясняем, что делать дальше.
    if len(sent) == 1 and sent[0].status == SubmissionStatus.pending and days_left > 2:
        return "pending", {**base, "task": sent[0].task.title}

    # 6. Начал и не дошёл до конца недели.
    done_ids = {x.task_id for x in sent}
    rest = [t for t in tasks if t.id not in done_ids]
    return "almost", {**base, "done": len(sent), "total": total,
                      "left_tasks": len(rest),
                      "possible": sum(max((o.points for o in t.options), default=t.points) for t in rest)}


async def nudge_targets(s) -> list[tuple[User, str, dict]]:
    """Кому и какую подсказку отправить сегодня."""
    out = []
    for user in await list_participants(s):
        if user.status != UserStatus.registered:
            continue
        picked = await nudge_for_user(s, user)
        if picked:
            out.append((user, picked[0], picked[1]))
    return out


async def task_number(s, task: Task) -> int:
    """Порядковый номер задания внутри своей недели: «Задание 1» … «Задание 4»."""
    week_tasks = [t for t in await list_tasks(s) if t.week == task.week]
    for i, t in enumerate(week_tasks, 1):
        if t.id == task.id:
            return i
    return 1


async def participant_rank(s, user_id: int) -> tuple[int | None, int]:
    """(place in the overall standings, number of participants ranked). Ties share a place."""
    points = await user_points_map(s)
    scores = []
    for u in await list_participants(s):
        if u.status == UserStatus.registered:
            scores.append((u.id, points.get(u.id, 0)))
    if not scores:
        return None, 0
    scores.sort(key=lambda r: -r[1])
    mine = dict(scores).get(user_id)
    if mine is None:
        return None, len(scores)
    place = sum(1 for _, pts in scores if pts > mine) + 1
    return place, len(scores)


async def top_participants(s, limit: int = 5) -> list[tuple[str, str, int]]:
    """Strongest participants: (name, team, points). Disqualified people are left out."""
    points = await user_points_map(s)
    rows = []
    for u in await list_participants(s):
        if u.status != UserStatus.registered:
            continue
        pts = points.get(u.id, 0)
        if pts:
            rows.append((u.display_name, f"{u.team.emoji} {u.team.name}" if u.team else "без команды", pts))
    rows.sort(key=lambda r: -r[2])
    return rows[:limit]
