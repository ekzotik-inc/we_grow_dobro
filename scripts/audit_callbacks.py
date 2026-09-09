"""Прогон всех кнопок через диспетчер на живой базе: ищем падения обработчиков."""
import sys
import asyncio, datetime, os, re
os.environ.update(BOT_TOKEN="123:test", DATABASE_URL="sqlite+aiosqlite:///./data/audit.db")
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update, Message, Chat, CallbackQuery, User as TgUser
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
from app.db import init_db, SessionLocal
from app import services
from app.bot.handlers import setup_routers

ADMIN, PC, USER = 1357560299, 101727102, 555
errors, unhandled = [], []

class FakeSession:
    """Отвечает на вызовы Telegram правдоподобными объектами, наружу ничего не уходит."""
    def __init__(self, bot): self.bot = bot
    async def __call__(self, bot, method, timeout=None):
        name = type(method).__name__
        if name in ("SendMessage", "SendPhoto", "EditMessageText", "EditMessageCaption", "EditMessageMedia"):
            return Message(message_id=1, date=datetime.datetime.now(),
                           chat=Chat(id=getattr(method, "chat_id", 1) or 1, type="private"),
                           from_user=TgUser(id=1, is_bot=True, first_name="bot"), text="ok")
        if name == "GetMe":
            return TgUser(id=1, is_bot=True, first_name="Добрик", username="we_grow_dobro_bot")
        if name in ("SendMediaGroup",):
            return []
        return True
    async def close(self): pass

async def build_state(s):
    for w in (1, 2, 3):
        await services.set_week_open(s, w, True)
    for tg, name in ((ADMIN, "Админ Админов"), (PC, "Дарья Писи"), (USER, "Иван Петров"), (556, "Пётр Второй")):
        u = await services.get_or_create_user(s, tg, f"u{tg}")
        await services.register_user(s, u, name, "IT", "Ташкент", phone="+998901234567")
        await services.approve_user(s, u, ADMIN)
    team = await services.create_team(s, None, "Добряки", "🔥")
    for tg in (USER, 556):
        await services.join_team(s, await services.get_user(s, tg), team.id)
    await s.commit()
    u = await services.get_user(s, USER)
    tasks = await services.list_tasks(s)
    sub = await services.start_submission(s, u, tasks[0], None)
    for i, st in enumerate(services.submission_steps(sub)):
        if st["kind"] == "note": await services.save_step_answer(s, sub, i, "текст ответа")
        else: await services.add_file(s, sub, {"type": "photo", "file_id": "f", "name": None}, step=i)
    await services.send_for_review(s, sub)
    await s.commit()
    return {"team": team.id, "task": tasks[0].id, "task_opt": next(t.id for t in tasks if t.options),
            "option": next(t.options[0].id for t in tasks if t.options), "sub": sub.id,
            "user_id": u.id, "pc_user_id": (await services.get_user(s, PC)).id}

def callbacks(ids):
    """Все callback_data, которые бот может показать, с реальными идентификаторами."""
    raw = set()
    src = open("app/keyboards.py", encoding="utf-8").read()
    for m in re.finditer(r'_btn\(\s*(?:f?"[^"]*"|[^,]+),\s*f?"([^"]*)"', src):
        raw.add(m.group(1))
    out = []
    for cb in sorted(raw):
        d = cb
        d = d.replace("{task.id}", str(ids["task"])).replace("{t.id}", str(ids["task"]))
        d = d.replace("{o.id}", str(ids["option"])).replace("{sub.id}", str(ids["sub"]))
        d = d.replace("{sub.task_id}", str(ids["task"])).replace("{team.id}", str(ids["team"]))
        d = d.replace("{t.number}", "1").replace("{w.number}", "1").replace("{task.week}", "1")
        d = d.replace("{u.id}", str(ids["user_id"])).replace("{sub.user_id}", str(ids["user_id"]))
        d = d.replace("{idx - 2}", "0").replace("{idx}", "1").replace("{index}", "0")
        d = d.replace("{i}", "0").replace("{n}", "1").replace("{page}", "0").replace("{code}", "m1")
        d = d.replace("{key}", "reg_channel_id").replace("{flag}", "registration_open")
        d = d.replace("{x}", "🔥").replace("{seg}", "all").replace("{team.id}", str(ids["team"]))
        if "{" in d:
            d = re.sub(r"\{[^}]*\}", "1", d)
        out.append((cb, d))
    return out

async def main():
    if os.path.exists("data/audit.db"): os.remove("data/audit.db")
    await init_db()
    async with SessionLocal() as s:
        ids = await build_state(s)
    bot = Bot("123:test", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session = FakeSession(bot)
    dp = Dispatcher(storage=MemoryStorage())
    root = setup_routers(); dp.include_router(root)

    seen = []
    for r in root.sub_routers:
        for obs in (r.message, r.callback_query):
            for h in obs.handlers:
                cb = h.callback
                def wrap(cb=cb, name=f"{r.name}.{cb.__name__}"):
                    async def inner(*a, **k):
                        seen.append(name)
                        return await cb(*a, **k)
                    return inner
                object.__setattr__(h, "callback", wrap())

    def upd(uid, data):
        return Update(update_id=1, callback_query=CallbackQuery(
            id="1", from_user=TgUser(id=uid, is_bot=False, first_name="U"), chat_instance="1", data=data,
            message=Message(message_id=1, date=datetime.datetime.now(), chat=Chat(id=uid, type="private"),
                            from_user=TgUser(id=uid, is_bot=False, first_name="U"), text="x")))

    cbs = callbacks(ids)
    print(f"кнопок для проверки: {len(cbs)}")
    for who, uid in (("админ", ADMIN), ("P&C", PC), ("участник", USER)):
        for raw, data in cbs:
            seen.clear()
            try:
                await dp.feed_update(bot, upd(uid, data))
            except Exception as ex:
                errors.append(f"{who}: {data} ({raw}) -> {type(ex).__name__}: {ex}")
                continue
            if not seen and who != "участник":
                unhandled.append(f"{who}: {data} ({raw})")
    print("ПАДЕНИЙ:", len(errors))
    for e in errors[:25]: print("  ✗", e)
    print("БЕЗ ОБРАБОТЧИКА (у сотрудников):", len(unhandled))
    for u in unhandled[:15]: print("  ?", u)
    os.remove("data/audit.db")

asyncio.run(main())
