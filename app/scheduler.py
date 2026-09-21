"""Scheduled jobs: week announcements, deadline reminders, P&C digest."""
from __future__ import annotations

import logging

import aiohttp
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, text

from . import keyboards as kb
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
    if not services.open_weeks():
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


# Куда ведёт кнопка под каждой личной подсказкой.
NUDGE_BUTTON = {
    "draft": ("tasks", "📝 Дописать отчёт"),
    "rejected": ("tasks", "🔁 Переделать"),
    "no_team": ("help", "🌱 Подобрать команду"),
    "zero": ("tasks", "📋 Задания недели"),
    "almost": ("tasks", "📋 Задания недели"),
    "all_done": ("teams", "🌱 Позвать коллег"),
    "pending": ("tasks", "📋 Взять ещё дело"),
}


async def nudge_job(bot: Bot) -> None:
    """Личная подсказка каждому участнику — по тому, где он сейчас застрял.

    Один человек получает не больше одного такого сообщения в день: задача запускается
    раз в сутки и сама выбирает для каждого ровно одну подсказку.
    """
    from .bot.handlers.admin import send_to_users

    if not services.open_weeks():
        return
    async with SessionLocal() as s:
        kind = f"nudge:{settings.today().isoformat()}"
        if await _already_sent(s, kind):
            return
        targets = await services.nudge_targets(s)
        if not targets:
            return
        sent = 0
        for user, code, ctx in targets:
            text = texts.nudge(code, ctx)
            if not text:
                continue
            cb, label = NUDGE_BUTTON.get(code, ("tasks", "📋 Задания недели"))
            sent += await send_to_users(bot, s, [user], text, f"{kind}:{code}",
                                        markup=kb.push_kb(cb, label))
        s.add(Broadcast(kind=kind, recipients=sent))
        await s.commit()
    log.info("личных подсказок отправлено: %s", sent)


async def last_call_job(bot: Bot, force: bool = False) -> int:
    """Вечер последнего дня недели: у каждого своя причина поторопиться.

    force — ручной запуск из панели: тогда отправляем и в другой день, и повторно.
    """
    from .bot.handlers.admin import send_to_users

    cw = services.current_week()
    if not cw or (not force and settings.today() != cw.end):
        return 0
    deadline = "%02d:%02d" % settings.close_at
    async with SessionLocal() as s:
        kind = f"lastcall:{cw.number}" + (f":manual:{settings.now():%Y-%m-%d %H:%M}" if force else "")
        if not force and await _already_sent(s, kind):
            return 0
        report = await services.stuck_report(s, cw.number)
        tasks = await services.list_tasks(s, cw.number)
        by_id = {t.id: t for t in tasks}
        sent = 0
        for user in await services.list_participants(s):
            if user.status.value != "registered":
                continue
            subs = [x for x in await services.user_submissions(s, user.id) if x.week == cw.number]
            draft = next((x for x in subs if x.status.value == "draft" and (x.files or x.answers)), None)
            done_ids = {x.task_id for x in subs
                        if x.status.value in ("pending", "approved")}
            rest = [t for t in by_id.values() if t.id not in done_ids]
            possible = sum(max((o.points for o in t.options), default=t.points) for t in rest)
            text = texts.last_call(cw.number, deadline, len(rest), possible,
                                   draft.task.title if draft else None)
            sent += await send_to_users(bot, s, [user], text, f"{kind}:{user.id}",
                                        markup=kb.push_kb("tasks", "📋 Успеть сегодня"))
        s.add(Broadcast(kind=kind, recipients=sent))
        await s.commit()
    log.info("«последний рывок» недели %s отправлен %s участникам (застряли: %s)",
             cw.number, sent, len(report["drafts"]))
    return sent


async def survey_report_job(bot: Bot) -> None:
    """Итоги опроса — админу и сотрудникам P&C, один раз в назначенное время."""
    moment = settings.survey_report_moment()
    if moment is None:
        return
    now = settings.now()
    if now < moment or (now - moment).total_seconds() > 3600:
        # Задача просыпается ежечасно: шлём в ближайший после назначенного времени час.
        return
    async with SessionLocal() as s:
        kind = f"survey_report:{services.SURVEY_CODE}:{moment:%Y-%m-%d %H:%M}"
        if await _already_sent(s, kind):
            return
        report = await services.survey_report(s)
        parts = texts.survey_report(report)
        staff = sorted(set(await services.list_admin_tg_ids(s)) | settings.admin_ids | settings.pc_ids)
        sent = 0
        for tg_id in staff:
            try:
                for part in parts:
                    await bot.send_message(tg_id, part)
                sent += 1
            except Exception as ex:  # noqa: BLE001
                log.warning("итоги опроса не ушли %s: %s", tg_id, ex)
        s.add(Broadcast(kind=kind, recipients=sent))
        await s.commit()
    log.info("итоги опроса отправлены %s сотрудникам", sent)


async def close_week_job(bot: Bot) -> None:
    """Закрыть неделю в её последний день: после этого отчёты не принимаются."""
    if not settings.auto_weeks:
        return
    week = settings.week_by_end(settings.today())
    if week is None or not services.week_is_open(week.number):
        return
    nxt = settings.week(week.number + 1)
    async with SessionLocal() as s:
        kind = f"week_closed:{week.number}"
        if await _already_sent(s, kind):
            return
        await services.set_week_open(s, week.number, False)
        await s.commit()
        n = await broadcast(bot, s, texts.week_closed(week.number, nxt.number if nxt else None,
                                                      "%02d:%02d" % settings.open_at), kind,
                            markup=kb.push_kb("top", "🏆 Посмотреть рейтинг"))
    log.info("неделя %s закрыта автоматически, уведомлено %s участников", week.number, n)


async def open_week_job(bot: Bot) -> None:
    """Открыть неделю в день её старта и сразу разослать анонс заданий."""
    if not settings.auto_weeks:
        return
    week = settings.week_by_start(settings.today())
    if week is None or services.week_is_open(week.number):
        return
    async with SessionLocal() as s:
        await services.set_week_open(s, week.number, True)
        await s.commit()
    log.info("неделя %s открыта автоматически", week.number)
    await announce_week_job(bot)


async def howto_job(bot: Bot) -> None:
    """Обучающая серия «что и куда»: по одной короткой инструкции через день."""
    if not services.open_weeks():
        return
    async with SessionLocal() as s:
        day = (settings.today() - settings.weeks[0].start).days
        if day < 0 or day % 2:
            return
        index = day // 2
        kind = f"howto:{index}"
        if await _already_sent(s, kind):
            return
        n = await broadcast(bot, s, texts.howto(index), kind, markup=kb.push_kb())
    log.info("инструкция %s отправлена %s участникам", index, n)


async def weekly_motivation_job(bot: Bot) -> None:
    """Еженедельная мотивация всем участникам — раз в неделю, в заданный день и час.

    Идёт независимо от того, включены ли недели: если заданий ещё нет, участник получает
    короткое «скоро начнём», а не тишину.
    """
    if settings.now().weekday() != settings.weekly_weekday:
        return
    async with SessionLocal() as s:
        kind = f"weekly:{settings.today().isoformat()}"
        if await _already_sent(s, kind):
            return
        n = await broadcast(bot, s, await weekly_text(s), kind)
    log.info("weekly motivation sent to %s users", n)


async def weekly_text(s) -> str:
    """Текст еженедельной рассылки: открытая неделя, её задания и тройка лидеров."""
    cw = services.current_week()
    tasks = await services.list_tasks(s, cw.number) if cw else []
    rows = await services.leaderboard(s)
    # Индекс завершающей фразы — по номеру недели года, чтобы формулировки не повторялись.
    index = settings.today().isocalendar()[1]
    return texts.weekly_motivation(cw.number if cw else None, tasks, rows, index)


async def top_digest_job(bot: Bot) -> None:
    """Standings for everyone, twice a week."""
    if not services.open_weeks():
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
    """Анонс уходит один раз на каждую открытую неделю — сразу после того, как её включил админ."""
    cw = services.current_week()
    if not cw:
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
    cw = services.current_week()
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
    """Не давать уснуть ни сервису, ни базе.

    Бесплатный хостинг усыпляет веб-сервис после 15 минут без входящих запросов, а Neon
    усыпляет базу уже через пять минут простоя. Разбудить базу первым же действием участника —
    значит заставить его ждать и, в худшем случае, получить ошибку вместо ответа.
    """
    if settings.db_keepalive_minutes > 0:
        async with SessionLocal() as s:
            try:
                await s.execute(text("SELECT 1"))
            except Exception as ex:  # noqa: BLE001
                log.warning("keepalive: база не ответила: %s", ex)
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
    sch.add_job(howto_job, CronTrigger(hour=settings.howto_hour, minute=0), args=[bot], id="howto")
    close_h, close_m = settings.close_at
    open_h, open_m = settings.open_at
    sch.add_job(last_call_job, CronTrigger(hour=settings.last_call_hour, minute=0), args=[bot], id="lastcall")
    # Отчёт по опросу — разовый, но задача проверяется каждый час: так он уйдёт даже если
    # сервис в назначенную минуту перезапускался.
    sch.add_job(survey_report_job, CronTrigger(minute=1), args=[bot], id="survey_report")
    sch.add_job(close_week_job, CronTrigger(hour=close_h, minute=close_m), args=[bot], id="week_close")
    sch.add_job(open_week_job, CronTrigger(hour=open_h, minute=open_m), args=[bot], id="week_open")
    log.info("смена недель: %s — закрытие, %s — открытие, автоматически: %s",
             settings.week_close_time, settings.week_open_time, "да" if settings.auto_weeks else "нет")
    sch.add_job(nudge_job, CronTrigger(hour=settings.nudge_hour, minute=0), args=[bot], id="nudge")
    sch.add_job(top_digest_job, CronTrigger(hour=settings.top_hour, minute=0), args=[bot], id="top")
    sch.add_job(weekly_motivation_job, CronTrigger(day_of_week=settings.weekly_weekday,
                                                   hour=settings.weekly_hour, minute=0),
                args=[bot], id="weekly")
    # Интервал по самому частому засыпанию: база — 5 минут, сервис — 15.
    minutes = settings.db_keepalive_minutes or 10
    sch.add_job(keepalive_job, IntervalTrigger(minutes=minutes), args=[bot], id="keepalive")
    log.info("keepalive каждые %s мин: база — %s, сервис — %s", minutes,
             "да" if settings.db_keepalive_minutes else "нет (спит, экономим квоту)",
             settings.external_url or "нет")
    return sch
