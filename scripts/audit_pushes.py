"""Проверка уведомлений участнику.

Каждое действие админа или сотрудника P&C прогоняется через диспетчер на живой базе,
а отправка в Telegram перехватывается. Проверяем одно: участник узнал о решении.

Запуск: python -m scripts.audit_pushes
"""
import asyncio
import datetime
import os
import sys

os.environ.update(BOT_TOKEN="123:test", DATABASE_URL="sqlite+aiosqlite:///./data/pushaudit.db")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aiogram import Bot, Dispatcher  # noqa: E402
from aiogram.client.default import DefaultBotProperties  # noqa: E402
from aiogram.enums import ParseMode  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.types import CallbackQuery, Chat, Message, Update  # noqa: E402
from aiogram.types import User as TgUser  # noqa: E402

from app import services  # noqa: E402
from app.bot.handlers import setup_routers  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402

ADMIN, PC, USER, MATE = 1357560299, 101727102, 555, 556
sent: list[tuple[int, str]] = []
problems: list[str] = []


class FakeSession:
    """Ничего наружу не уходит: запоминаем, кому и что бот отправил."""

    def __init__(self, bot):
        self.bot = bot

    async def __call__(self, bot, method, timeout=None):
        name = type(method).__name__
        if name in ("SendMessage", "SendPhoto"):
            sent.append((int(getattr(method, "chat_id", 0) or 0),
                         getattr(method, "text", None) or getattr(method, "caption", "") or ""))
        if name in ("SendMessage", "SendPhoto", "EditMessageText", "EditMessageCaption", "EditMessageMedia"):
            return Message(message_id=1, date=datetime.datetime.now(),
                           chat=Chat(id=getattr(method, "chat_id", 1) or 1, type="private"),
                           from_user=TgUser(id=1, is_bot=True, first_name="bot"), text="ok")
        if name == "GetMe":
            return TgUser(id=1, is_bot=True, first_name="Добрик", username="we_grow_dobro_bot")
        if name == "SendMediaGroup":
            return []
        return True

    async def close(self):
        pass


def cq_update(uid: int, data: str) -> Update:
    return Update(update_id=1, callback_query=CallbackQuery(
        id="1", from_user=TgUser(id=uid, is_bot=False, first_name="S"), chat_instance="1", data=data,
        message=Message(message_id=1, date=datetime.datetime.now(), chat=Chat(id=uid, type="private"),
                        from_user=TgUser(id=uid, is_bot=False, first_name="S"), text="x")))


def msg_update(uid: int, text: str) -> Update:
    return Update(update_id=2, message=Message(
        message_id=2, date=datetime.datetime.now(), chat=Chat(id=uid, type="private"),
        from_user=TgUser(id=uid, is_bot=False, first_name="S"), text=text))


async def act(dp, bot, actor: int, *steps) -> None:
    """Выполнить последовательность действий сотрудника: строка — текст, иначе callback."""
    for step in steps:
        upd = msg_update(actor, step[1:]) if step.startswith(">") else cq_update(actor, step)
        await dp.feed_update(bot, upd)


def check(title: str, target: int, must_contain: str = "") -> None:
    """Убедиться, что участник получил сообщение (и в нём есть нужное слово)."""
    got = [t for chat, t in sent if chat == target]
    if not got:
        problems.append(f"{title}: участник {target} НЕ получил уведомление")
        print(f"  ✗ {title}")
        return
    if must_contain and not any(must_contain.lower() in t.lower() for t in got):
        problems.append(f"{title}: в уведомлении нет «{must_contain}»")
        print(f"  ✗ {title} — нет «{must_contain}»")
        return
    first = got[0].split("\n")[0][:60]
    print(f"  ✓ {title} → «{first}»")


async def ready_submission(user_tg: int, week: int = 1, index: int = 0) -> int:
    """Отчёт участника, дошедший до проверки."""
    async with SessionLocal() as s:
        u = await services.get_user(s, user_tg)
        task = [t for t in await services.list_tasks(s, week) if not t.has_options][index]
        sub = await services.start_submission(s, u, task, None)
        for i, st in enumerate(services.submission_steps(sub)):
            if st["kind"] == "note":
                await services.save_step_answer(s, sub, i, "ответ участника")
            else:
                await services.add_file(s, sub, {"type": "photo", "file_id": "f", "name": None}, step=i)
        await services.send_for_review(s, sub)
        await s.commit()
        return sub.id


async def build_state() -> dict:
    async with SessionLocal() as s:
        for w in (1, 2, 3):
            await services.set_week_open(s, w, True)
        await s.commit()
        await services.load_open_weeks(s)
        for tg, name in ((ADMIN, "Админ Админов"), (PC, "Дарья Писи"),
                         (USER, "Иван Петров"), (MATE, "Пётр Второй")):
            u = await services.get_or_create_user(s, tg, f"u{tg}")
            await services.register_user(s, u, name, "IT", "Ташкент", phone="+998901234567")
            if tg != USER:
                await services.approve_user(s, u, ADMIN)
        team = await services.create_team(s, None, "Добряки", "🔥")
        await services.join_team(s, await services.get_user(s, MATE), team.id)
        await s.commit()
        return {"team": team.id, "user_id": (await services.get_user(s, USER)).id,
                "mate_id": (await services.get_user(s, MATE)).id}


async def main() -> None:
    if os.path.exists("data/pushaudit.db"):
        os.remove("data/pushaudit.db")
    await init_db()
    ids = await build_state()

    bot = Bot("123:test", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    bot.session = FakeSession(bot)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(setup_routers())

    uid, team = ids["user_id"], ids["team"]

    print("ЗАЯВКА НА УЧАСТИЕ")
    sent.clear()
    await act(dp, bot, PC, f"mod:ok:{uid}")            # «Принять» — сотрудник P&C
    check("заявку приняли (P&C)", USER, "принят")

    async with SessionLocal() as s:                     # вернуть в ожидание для отказа
        u = await services.get_user(s, USER)
        u.status = services.UserStatus.pending
        await s.commit()
    sent.clear()
    await act(dp, bot, ADMIN, f"mod:rej:{uid}", f"mod:rej_r:{uid}:m2")
    check("заявку отклонили (админ)", USER, "отклонена")

    async with SessionLocal() as s:
        u = await services.get_user(s, USER)
        u.status = services.UserStatus.pending
        await s.commit()
    await act(dp, bot, ADMIN, f"mod:ok:{uid}")

    print("\nОТЧЁТЫ")
    sub_id = await ready_submission(USER, 1, 0)
    sent.clear()
    await act(dp, bot, PC, f"adm:ok:{sub_id}")
    check("отчёт зачтён (P&C)", USER, "зачтено")

    sub_id = await ready_submission(USER, 1, 1)
    sent.clear()
    await act(dp, bot, ADMIN, f"adm:rej:{sub_id}", f"adm:rej_r:{sub_id}:r1")
    check("отчёт отклонён (админ)", USER, "не зачтено")

    print("\nКОМАНДА")
    sent.clear()
    await act(dp, bot, ADMIN, f"adm:move_to:{uid}:{team}")
    check("перевод в команду", USER, "команде")
    sent.clear()
    await act(dp, bot, ADMIN, f"adm:move_to:{uid}:0")
    check("убрали из команды", USER)
    sent.clear()
    await act(dp, bot, ADMIN, f"adm:move_to:{uid}:{team}")
    sent.clear()
    await act(dp, bot, ADMIN, f"adm:team_rename:{team}", ">Добродеи")
    check("команду переименовали", USER)

    print("\nБАЛЛЫ ВРУЧНУЮ (владелец, /addresult)")
    sub_id = await ready_submission(USER, 2, 0)
    await act(dp, bot, ADMIN, f"adm:ok:{sub_id}")
    sent.clear()
    await act(dp, bot, ADMIN, f"res:add:{uid}", ">300 за помощь с организацией")
    check("начислили баллы", USER, "300")
    sent.clear()
    await act(dp, bot, ADMIN, f"res:revoke:{sub_id}", ">ошибка проверки")
    check("отменили зачёт", USER)
    sent.clear()
    await act(dp, bot, ADMIN, f"res:wipe_user:{uid}", f"res:wipe_user_ok:{uid}")
    check("обнулили результаты участника", USER)

    # Отдельно: отчёты есть, а баллов нет. Раньше в этом случае бот молчал,
    # хотя отправленные дела удалялись.
    sub_id = await ready_submission(USER, 2, 1)
    await act(dp, bot, ADMIN, f"adm:rej:{sub_id}", f"adm:rej_r:{sub_id}:r3")
    sent.clear()
    await act(dp, bot, ADMIN, f"res:wipe_user:{uid}", f"res:wipe_user_ok:{uid}")
    check("обнулили отчёты без баллов", USER, "отчёт")

    print("\nСТАТУС УЧАСТНИКА")
    sent.clear()
    await act(dp, bot, ADMIN, f"adm:dq:{uid}", ">нарушение правил")
    check("дисквалификация", USER, "правил")
    sent.clear()
    await act(dp, bot, ADMIN, f"adm:reinstate:{uid}")
    check("восстановление", USER)

    print("\nКОМАНДА ЦЕЛИКОМ")
    # Прошлые шаги уже обнулили участника — даём ему свежий зачтённый отчёт,
    # иначе обнулять будет нечего и молчание бота окажется правильным.
    sub_id = await ready_submission(USER, 3, 0)
    await act(dp, bot, ADMIN, f"adm:ok:{sub_id}")
    sent.clear()
    await act(dp, bot, ADMIN, f"res:wipe_team:{team}", f"res:wipe_team_ok:{team}")
    check("обнулили результаты команды", USER)
    sent.clear()
    await act(dp, bot, ADMIN, f"adm:team_del:{team}", f"adm:team_del_ok:{team}")
    check("команду расформировали", USER)

    print("\nРАССЫЛКИ ДЛЯ АКТИВНОСТИ")
    sent.clear()
    await act(dp, bot, ADMIN, "adm:howto")
    check("инструкция дня", USER)
    sent.clear()
    await act(dp, bot, ADMIN, "adm:nudge")
    check("личная подсказка", USER)
    sent.clear()
    await act(dp, bot, ADMIN, "adm:weekly")
    check("мотивация недели", USER)

    print("\nУДАЛЕНИЕ")
    sent.clear()
    await act(dp, bot, ADMIN, f"adm:del:{uid}", f"adm:del_ok:{uid}")
    check("участника удалили из бота", USER)

    await bot.session.close()
    print(f"\nПРОБЛЕМ: {len(problems)}")
    for p in problems:
        print("  •", p)
    if os.path.exists("data/pushaudit.db"):
        os.remove("data/pushaudit.db")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    asyncio.run(main())
