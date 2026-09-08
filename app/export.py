"""Excel export for P&C (participants, teams, submissions, points log)."""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font
from sqlalchemy import select

from . import services
from .models import PointsLog, User


def _sheet(wb: Workbook, title: str, headers: list[str], rows: list[list]) -> None:
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, name="Arial")
    for r in rows:
        ws.append(r)
    for col in ws.columns:
        width = max((len(str(c.value)) if c.value is not None else 0) for c in col)
        ws.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 60)


async def export_xlsx(s, path: Path) -> Path:
    wb = Workbook()
    wb.remove(wb.active)

    rows = await services.leaderboard(s)
    _sheet(
        wb,
        "Команды",
        ["Место", "Команда", "Баллы", "Участников", "Состав"],
        [[i, f"{r['team'].emoji} {r['team'].name}", r["points"], len(r["members"]), ", ".join(m.display_name for m in r["members"])] for i, r in enumerate(rows, 1)],
    )

    users = await services.list_participants(s)
    upts = await services.user_points_map(s)
    _sheet(
        wb,
        "Участники",
        ["ФИО", "Username", "TG id", "Отдел", "Город", "Команда", "Статус", "Баллы", "Причина дисквалификации"],
        [
            [u.full_name, u.username, u.tg_id, u.department, u.city, u.team.name if u.team else "", u.status.value, upts.get(u.id, 0), u.disqualified_reason]
            for u in users
        ],
    )

    subs = []
    for u in users:
        subs.extend(await services.user_submissions(s, u.id))
    _sheet(
        wb,
        "Отчёты",
        ["ID", "Участник", "Команда", "Неделя", "№ задания", "Задание", "Опция", "Статус", "Баллы", "Файлов", "Заметка", "Комментарий P&C", "Отправлен", "Проверен"],
        [
            [
                x.id,
                x.user.display_name,
                x.user.team.name if x.user.team else "",
                x.week,
                x.task.code,
                x.task.title,
                x.option.title if x.option else "",
                x.status.value,
                x.points_awarded,
                len(x.files or []),
                x.note,
                x.review_comment,
                x.submitted_at.strftime("%d.%m.%Y %H:%M") if x.submitted_at else "",
                x.reviewed_at.strftime("%d.%m.%Y %H:%M") if x.reviewed_at else "",
            ]
            for x in subs
        ],
    )

    logs = (await s.execute(select(PointsLog, User).join(User, User.id == PointsLog.user_id).order_by(PointsLog.created_at))).all()
    _sheet(
        wb,
        "Журнал баллов",
        ["Дата", "Участник", "Изменение", "Причина", "Кто (tg id)"],
        [[l.created_at.strftime("%d.%m.%Y %H:%M"), u.display_name, l.delta, l.reason, l.actor_tg_id] for l, u in logs],
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
