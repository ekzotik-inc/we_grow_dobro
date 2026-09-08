"""Scheduled jobs: week announcements, deadline reminders, P&C digest."""
from __future__ import annotations

import logging

import aiohttp
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from . import services, texts
from .bot.handlers.admin import broadcast
from .config import settings
from .db import SessionLocal
from .models import Broadcast

log = logging.getLogger(__name__)


async def _already_sent(s, kind: str) -> bool:
    return (await s.execute(select(Broadcast.id).where(Broadcast.kind == kind))).first() is not None


async def motivation_job(bot: Bot) -> None:
    """A short nudge every other day, so the marathon stays present without becoming noise."""
    if settings.marathon_status() != "active":
        return
    async with SessionLocal() as s:
        day = (settings.today() - settings.weeks[0].start).days
        if day % 2:  # every second day only
            return
        kind = f"motivation:{settings.today().isoformat()}"
        if await _already_sent(s, kind):
            return
        n = await broadcast(bot, s, texts.motivation(day // 2), kind)
    log.info("motivation sent to %s users", n)


async def top_digest_job(bot: Bot) -> None:
    """Standings for everyone, twice a week."""
    if settings.marathon_status() != "active":
        return
    if settings.now().weekday() not in (2, 6):  # Wednesday and Sunday
        return
    async with SessionLocal() as s:
        kind = f"top:{settings.today().isoformat()}"
        if await _already_sent(s, kind):
            return
        rows = await services.leaderboard(s)
        people = await services.top_participants(s)
        if not rows:
            return
        n = await broadcast(bot, s, texts.top_digest(rows, people), kind)
    log.info("top digest sent to %s users", n)


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
        n = await broadcast(bot, s, texts.week_announce(cw.number, tasks), kind, image=f"week{cw.number}.png")
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
        admins = await services.list_admin_tg_ids(s)
    warning = texts.db_expiry_warning()
    if not pending and not apps and not warning:
        return
    parts = []
    if apps:
        parts.append(f"🙋 заявок на модерации: <b>{apps}</b>")
    if pending:
        parts.append(f"🔎 отчётов на проверке: <b>{pending}</b>")
    text = ("🛠 Напоминание P&C — " + ", ".join(parts) + ". Откройте /admin.") if parts else ""
    if warning:
        text = (text + "\n\n" if text else "") + warning
    for a in admins:
        try:
            await bot.send_message(a, text)
        except Exception as ex:  # noqa: BLE001
            log.warning("digest to %s failed: %s", a, ex)


async def keepalive_job(bot: Bot) -> None:
    """Free hosting sleeps a web service after 15 minutes without inbound traffic, which stops the bot.
    Requesting our own health endpoint over the public URL counts as inbound traffic and keeps it awake."""
    if not settings.external_url:
        return
    url = f"{settings.external_url}/api/health"
    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                log.debug("keepalive ping %s -> %s", url, resp.status)
    except Exception as ex:  # noqa: BLE001
        log.warning("keepalive ping failed: %s", ex)


def build_scheduler(bot: Bot) -> AsyncIOScheduler:
    sch = AsyncIOScheduler(timezone=settings.tz)
    sch.add_job(announce_week_job, CronTrigger(hour=settings.announce_hour, minute=0), args=[bot], id="announce")
    sch.add_job(reminder_job, CronTrigger(hour=settings.reminder_hour, minute=0), args=[bot], id="reminder")
    sch.add_job(admin_digest_job, CronTrigger(hour=18, minute=0), args=[bot], id="digest")
    sch.add_job(motivation_job, CronTrigger(hour=settings.motivation_hour, minute=0), args=[bot], id="motivation")
    sch.add_job(top_digest_job, CronTrigger(hour=settings.top_hour, minute=0), args=[bot], id="top")
    if settings.external_url:
        sch.add_job(keepalive_job, IntervalTrigger(minutes=10), args=[bot], id="keepalive")
        log.info("keepalive enabled: pinging %s/api/health every 10 minutes", settings.external_url)
    return sch
