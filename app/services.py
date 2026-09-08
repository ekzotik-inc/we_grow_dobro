"""Business logic. Every function takes an AsyncSession and never touches Telegram."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from .config import settings
from .models import PointsLog, Submission, SubmissionStatus, Task, Team, User, UserStatus


class ServiceError(Exception):
    """User-facing error (message is shown to the user)."""


# ---------- users ----------

async def get_or_create_user(s, tg_id: int, username: str | None) -> User:
    user = (await s.execute(select(User).where(User.tg_id == tg_id))).scalar_one_or_none()
    if user is None:
        user = User(tg_id=tg_id, username=username, is_admin=settings.is_admin(tg_id))
        s.add(user)
        await s.flush()
    else:
        changed = False
        if username != user.username:
            user.username = username
            changed = True
        if settings.is_admin(tg_id) and not user.is_admin:
            user.is_admin = True
            changed = True
        if changed:
            await s.flush()
    return user


async def get_user(s, tg_id: int) -> User | None:
    return (
        await s.execute(select(User).options(selectinload(User.team)).where(User.tg_id == tg_id))
    ).scalar_one_or_none()


async def get_user_by_id(s, user_id: int) -> User | None:
    return (await s.execute(select(User).options(selectinload(User.team)).where(User.id == user_id))).scalar_one_or_none()


async def register_user(s, user: User, full_name: str, department: str | None, city: str | None) -> None:
    user.full_name = full_name.strip()[:160]
    user.department = (department or "").strip()[:160] or None
    user.city = (city or "").strip()[:80] or None
    user.status = UserStatus.registered
    user.rules_accepted_at = datetime.utcnow()
    await s.flush()


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
    ids = set(settings.admin_ids)
    rows = (await s.execute(select(User.tg_id).where(User.is_admin.is_(True)))).scalars()
    ids.update(rows)
    return ids


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


async def create_team(s, user: User, name: str, emoji: str = "🌱") -> Team:
    name = " ".join(name.split())[:80]
    if len(name) < 2:
        raise ServiceError("Название команды слишком короткое.")
    if user.team_id:
        raise ServiceError("Вы уже состоите в команде. Сначала покиньте её.")
    # SQLite's lower() is ASCII-only, so compare case-insensitively in Python (teams are few).
    names = (await s.execute(select(Team.name))).scalars()
    if any(n.casefold() == name.casefold() for n in names):
        raise ServiceError("Команда с таким названием уже есть. Выберите другое название.")
    team = Team(name=name, emoji=emoji, captain_id=user.id)
    s.add(team)
    await s.flush()
    user.team_id = team.id
    await s.flush()
    return team


async def join_team(s, user: User, team_id: int) -> Team:
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
    if settings.marathon_status() != "before" and not settings.force_week:
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


async def get_task(s, task_id: int) -> Task | None:
    return (await s.execute(select(Task).options(selectinload(Task.options)).where(Task.id == task_id))).scalar_one_or_none()


def task_is_open(task: Task) -> bool:
    cw = settings.current_week()
    return cw is not None and cw.number == task.week


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


async def user_submissions(s, user_id: int) -> list[Submission]:
    return list((await s.execute(_sub_query().where(Submission.user_id == user_id).order_by(Submission.week, Submission.task_id))).scalars())


async def start_submission(s, user: User, task: Task, option_id: int | None) -> Submission:
    if user.status != UserStatus.registered:
        raise ServiceError("Вы не являетесь активным участником марафона.")
    if not user.team_id:
        raise ServiceError("Сначала вступите в команду — задания выполняются в командном зачёте.")
    if not task_is_open(task):
        raise ServiceError("Это задание можно выполнять только в его неделю.")
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
    # rejected / cancelled / draft -> reset and start over
    sub.option_id = option.id if option else None
    sub.status = SubmissionStatus.draft
    sub.note = None
    sub.files = []
    sub.points_awarded = 0
    sub.review_comment = None
    await s.flush()
    return await get_submission(s, sub.id)


async def add_file(s, sub: Submission, file: dict) -> int:
    files = list(sub.files or [])
    if len(files) >= 10:
        raise ServiceError("Максимум 10 файлов в одном отчёте.")
    files.append(file)
    sub.files = files
    await s.flush()
    return len(files)


async def set_note(s, sub: Submission, note: str) -> None:
    sub.note = note.strip()[:2000]
    await s.flush()


def submission_missing(sub: Submission) -> list[str]:
    missing = []
    photos = len(sub.files or [])
    if photos < sub.required_photos:
        missing.append(f"файлов: {photos} из {sub.required_photos}")
    if sub.task.note_required and not (sub.note or "").strip():
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
    return (
        await s.execute(
            select(func.coalesce(func.sum(Submission.points_awarded), 0)).where(
                Submission.user_id == user_id, Submission.status == SubmissionStatus.approved
            )
        )
    ).scalar_one()


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
    return {tid: int(pts) for tid, pts in rows}


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
    return {uid: int(p) for uid, p in rows}


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
