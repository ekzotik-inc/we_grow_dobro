"""Scheduled jobs: week announcements, deadline reminders, P&C digest."""
from __future__ import annotations

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from . import services, texts
from .bot.handlers.admin import broadcast
from .config import settings
from .db import SessionLocal
from .models import Broadcast

log = logging.getLogger(__name__)


async def _already_sent(s, kind: str) -> bool:
    return (await s.execute(select(Broadcast.id).where(Broadcast.kind == kind))).first() is not None


async def announce_week_job(bot: Bot) -> None:
    """Runs every day at ANNOUNCE_HOUR; sends the announcement once on the first day of each week."""
    cw = settings.current_week()
    if not cw or settings.today() != cw.start and not settings.force_week:
        return
    kind = f"week_announce:{cw.number}"
    async with SessionLocal() as s:
        if await _already_sent(s, kind):
            return
        tasks = await services.list_tasks(s, cw.number)
        n = await broadcast(bot, s, texts.week_announce(cw.number, tasks), kind)
    log.info("week %s announced to %s users", cw.number, n)


async def reminder_job(bot: Bot) -> None:
    """Day before the week's deadline: nudge participants who have not submitted anything this week."""
    cw = settings.current_week()
    if not cw:
        return
    days_left = (cw.end - settings.today()).days
    if days_left != 1:
        return
    kind = f"reminder:{cw.number}"
    async with SessionLocal() as s:
        if await _already_sent(s, kind):
            return
        idle_ids = {u.id for u in await services.users_without_submissions(s, cw.number)}
        n = await broadcast(bot, s, texts.week_reminder(cw.number), kind, user_filter=lambda u: u.id in idle_ids)
    log.info("reminder for week %s sent to %s users", cw.number, n)


async def admin_digest_job(bot: Bot) -> None:
    async with SessionLocal() as s:
        pending = await services.pending_count(s)
        apps = await services.pending_users_count(s)
        if not pending and not apps:
            return
        admins = await services.list_admin_tg_ids(s)
    parts = []
    if apps:
        parts.append(f"🙋 заявок на модерации: <b>{apps}</b>")
    if pending:
        parts.append(f"🔎 отчётов на проверке: <b>{pending}</b>")
    text = "🛠 Напоминание P&C — " + ", ".join(parts) + ". Откройте /admin."
    for a in admins:
        try:
            await bot.send_message(a, text)
        except Exception as ex:  # noqa: BLE001
            log.warning("digest to %s failed: %s", a, ex)


def build_scheduler(bot: Bot) -> AsyncIOScheduler:
    sch = AsyncIOScheduler(timezone=settings.tz)
    sch.add_job(announce_week_job, CronTrigger(hour=settings.announce_hour, minute=0), args=[bot], id="announce")
    sch.add_job(reminder_job, CronTrigger(hour=settings.reminder_hour, minute=0), args=[bot], id="reminder")
    sch.add_job(admin_digest_job, CronTrigger(hour=18, minute=0), args=[bot], id="digest")
    return sch
