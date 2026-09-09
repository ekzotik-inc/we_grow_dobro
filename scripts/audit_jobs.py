"""Фоновые задачи и работа с каналами — выполняем по-настоящему, с подставным Telegram."""
import sys
import asyncio, datetime, os
os.environ.update(BOT_TOKEN="123:test", DATABASE_URL="sqlite+aiosqlite:///:memory:")
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import Message, Chat, User as TgUser
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
from app.db import init_db, SessionLocal
from app import services, scheduler as sched
from app.bot import channels

ADMIN, PC = 1357560299, 101727102
sent = []

class FakeSession:
    def __init__(self, bot): self.bot = bot
    async def __call__(self, bot, method, timeout=None):
        name = type(method).__name__
        sent.append(name)
        if name in ("SendMessage","SendPhoto","EditMessageText","EditMessageCaption","EditMessageMedia"):
            return Message(message_id=len(sent), date=datetime.datetime.now(),
                           chat=Chat(id=getattr(method,"chat_id",1) or 1, type="private"),
                           from_user=TgUser(id=1,is_bot=True,first_name="bot"), text="ok")
        if name == "GetMe": return TgUser(id=1,is_bot=True,first_name="bot",username="bot")
        if name == "SendMediaGroup": return []
        return True
    async def close(self): pass

bad=[]
def check(ok, what):
    print(("  ✓ " if ok else "  ✗ ") + what)
    if not ok: bad.append(what)

async def main():
    await init_db()
    bot = Bot("123:test", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session = FakeSession(bot)
    async with SessionLocal() as s:
        for w in (1,2,3): await services.set_week_open(s, w, True)
        u = await services.get_or_create_user(s, 500, "ivan")
        await services.register_user(s, u, "Иван Петров", "IT", "Ташкент", phone="+998901234567")
        await services.approve_user(s, u, ADMIN)
        team = await services.create_team(s, None, "Добряки", "🔥"); await services.join_team(s, u, team.id)
        await s.commit()
        task = (await services.list_tasks(s,1))[0]
        sub = await services.start_submission(s, u, task, None)
        for i, st in enumerate(services.submission_steps(sub)):
            if st["kind"]=="note": await services.save_step_answer(s, sub, i, "текст")
            else: await services.add_file(s, sub, {"type":"photo","file_id":"f","name":None}, step=i)
        await services.send_for_review(s, sub); await s.commit()
        sub = await services.get_submission(s, sub.id)

        print("КАНАЛЫ")
        for name, coro in (
            ("карточка заявки", channels.post_registration(bot, s, u)),
            ("обновление карточки заявки", channels.update_registration_post(bot, s, u)),
            ("карточка отчёта", channels.post_submission(bot, s, sub)),
            ("обновление карточки отчёта", channels.update_submission_post(bot, s, sub)),
            ("уведомление P&C", channels.notify_pc(bot, s, "тест")),
            ("уведомление админам", channels.notify_admins(bot, s, "тест")),
        ):
            try:
                await coro; check(True, name)
            except Exception as ex: check(False, f"{name}: {type(ex).__name__}: {ex}")
        await s.commit()

    print("ФОНОВЫЕ ЗАДАЧИ")
    for name, job in (("анонс недели", sched.announce_week_job), ("напоминание", sched.reminder_job),
                      ("сводка админам", sched.admin_digest_job), ("мотивация", sched.motivation_job),
                      ("топ участников", sched.top_digest_job), ("keepalive", sched.keepalive_job)):
        try:
            await job(bot); check(True, name)
        except Exception as ex: check(False, f"{name}: {type(ex).__name__}: {ex}")

    print("ЭКСПОРТ И РАССЫЛКИ")
    from app.export import export_xlsx
    import pathlib
    async with SessionLocal() as s:
        try:
            path = await export_xlsx(s, pathlib.Path("data/audit_export.xlsx")); check(path.exists(), "экспорт в Excel")
            path.unlink()
        except Exception as ex: check(False, f"экспорт: {ex}")
        for code, _t, _a in services.SEGMENTS:
            try:
                await services.segment_users(s, code, "2" if code=="lt_n_week" else ("1" if code=="team" else None))
            except Exception as ex: check(False, f"сегмент {code}: {ex}")
        check(True, f"все {len(services.SEGMENTS)} сегментов рассылки считаются")
    await bot.session.close()
    print("ПРОБЛЕМ:", len(bad))
asyncio.run(main())
