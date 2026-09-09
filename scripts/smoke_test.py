"""End-to-end check of the business logic without Telegram.

Run:  BOT_TOKEN=test:token DATABASE_URL=sqlite+aiosqlite:///./data/smoke.db python -m scripts.smoke_test
"""
from __future__ import annotations

import asyncio
import os

os.environ.setdefault("BOT_TOKEN", "123:test-token")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/smoke.db")
os.environ.setdefault("ADMIN_IDS", "999")

from fastapi.testclient import TestClient  # noqa: E402

from app import services  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal, engine, init_db  # noqa: E402
from app.export import export_xlsx  # noqa: E402
from app.models import Base, SubmissionStatus, UserStatus  # noqa: E402
from app.web.api import app  # noqa: E402


async def reset_schema() -> None:
    """Start from an empty database on any backend: deleting a file only works for SQLite."""
    if settings.database_url.startswith("sqlite"):
        path = settings.database_url.split("///")[-1]
        if path and path != ":memory:" and os.path.exists(path):
            os.remove(path)
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


def check_no_plain_emoji() -> None:
    """Every emoji the project can show must have a premium counterpart.

    The bot upgrades plain emoji on the way out (app/bot/middlewares.py), so a character missing
    from both sets is the only way a plain one could reach a participant.
    """
    import glob
    import re as _re
    from pathlib import Path
    from app import emoji as em

    pattern = _re.compile(
        "[\U0001F000-\U0001FAFF\u2190-\u21FF\u2300-\u27BF\u2B00-\u2BFF\u2600-\u26FF]"
        "[\ufe0f\u200d\U0001F000-\U0001FAFF]*"
    )
    # Typography, not emoji: these are drawn as text and must stay text.
    typography = {"→", "←", "─", "·", "—", "▸"}
    plain: dict[str, str] = {}
    sources = [p for p in glob.glob("app/**/*.py", recursive=True) if not p.endswith("emoji.py")]
    for path in sources + ["data/tasks.json"]:
        for found in pattern.findall(Path(path).read_text(encoding="utf-8")):
            if found.strip() and found not in typography and em._canon(found) is None:
                plain.setdefault(found, path)
    assert not plain, f"эмодзи без премиум-версии: {plain}"

    sample = em.rich("📋 Задания ⚡ и 🏆 рейтинг")
    assert "<tg-emoji" in sample and _re.sub(r"<tg-emoji[^>]*>.*?</tg-emoji>", "", sample).strip() == "Задания  и  рейтинг"
    print("emoji ok: обычных эмодзи не осталось,", len(em.CHARS), "символов в наборе")


async def fill_steps(s, sub) -> None:
    """Пройти пошаговый мастер целиком: на фото-шаги кладём файл, на текстовые — ответ."""
    steps = services.submission_steps(sub)
    if not steps:
        for i in range(sub.required_photos):
            await services.add_file(s, sub, {"type": "photo", "file_id": f"f{sub.id}-{i}", "name": None})
        await services.set_note(s, sub, "Готово")
        return
    for i, st in enumerate(steps):
        if st.get("kind") == "note":
            await services.save_step_answer(s, sub, i, f"Ответ на шаг {i + 1}")
        else:
            await services.add_file(s, sub, {"type": "photo", "file_id": f"f{sub.id}-{i}", "name": None}, step=i)


def check_phone_parsing() -> None:
    """Номер должен приниматься и с кодом страны, и без него, а имя — не приниматься как номер."""
    from app.bot.handlers.start import _clean_phone, _looks_like_phone

    assert _clean_phone("+998 90 123 45 67") == "+998901234567"
    assert _clean_phone("90 123 45 67") == "+998901234567", "местный номер дополняем кодом страны"
    assert _clean_phone("901234567") == "+998901234567"
    assert _clean_phone("Иван Петров") is None
    assert _looks_like_phone("+998 90 123 45 67") and not _looks_like_phone("Иван Петров")
    print("phone parsing ok")


def check_admin_access() -> None:
    """Панель открыта только по спискам ADMIN_IDS / PC_IDS, флаг в базе прав не даёт."""
    assert settings.is_admin(999), "админ из ADMIN_IDS должен иметь доступ"
    assert not settings.is_admin(555), "обычный участник не должен иметь доступ"
    from app.main import PARTICIPANT_COMMANDS, STAFF_COMMANDS

    assert "admin" not in [c.command for c in PARTICIPANT_COMMANDS], "участник не должен видеть /admin"
    assert "admin" in [c.command for c in STAFF_COMMANDS]


_DISPATCHER = None


def build_dispatcher():
    """Диспетчер собирается один раз: роутеры — модульные объекты и к двум диспетчерам не крепятся."""
    global _DISPATCHER
    if _DISPATCHER is None:
        from aiogram import Dispatcher
        from aiogram.fsm.storage.memory import MemoryStorage

        from app.bot.handlers import setup_routers

        dp = Dispatcher(storage=MemoryStorage())
        root = setup_routers()
        dp.include_router(root)
        _DISPATCHER = (dp, root)
    return _DISPATCHER


async def check_admin_router_blocks() -> None:
    """Через диспетчер: участник не должен попадать ни в /admin, ни в кнопки панели.

    Проверяем именно так, а не вызовом фильтра: aiogram не ждёт результат фильтра,
    объявленного обычным классом, и однажды панель из-за этого открылась всем.
    """
    import datetime

    from aiogram import Bot
    from aiogram.types import CallbackQuery, Chat, Message, Update
    from aiogram.types import User as TgUser

    bot = Bot("123:test-token")
    dp, root = build_dispatcher()

    seen: list[str] = []
    # Подменяем обработчики заглушками, чтобы видеть, кто сработал, и обязательно возвращаем
    # оригиналы: роутеры общие на весь процесс, и следующие проверки работали бы с заглушками.
    originals = []
    for r in root.sub_routers:
        for obs in (r.message, r.callback_query):
            for h in obs.handlers:
                originals.append((h, h.callback))
                def wrap(name=f"{r.name}.{h.callback.__name__}"):
                    async def inner(*a, **k):
                        seen.append(name)
                    return inner
                object.__setattr__(h, "callback", wrap())

    def message_update(uid: int, text: str) -> Update:
        return Update(update_id=1, message=Message(
            message_id=1, date=datetime.datetime.now(), chat=Chat(id=uid, type="private"),
            from_user=TgUser(id=uid, is_bot=False, first_name="U"), text=text))

    def callback_update(uid: int, data: str) -> Update:
        return Update(update_id=2, callback_query=CallbackQuery(
            id="1", from_user=TgUser(id=uid, is_bot=False, first_name="U"), chat_instance="1", data=data,
            message=Message(message_id=1, date=datetime.datetime.now(), chat=Chat(id=uid, type="private"),
                            from_user=TgUser(id=uid, is_bot=False, first_name="U"), text="x")))

    stranger = 555
    for update in (message_update(stranger, "/admin"), callback_update(stranger, "adm"),
                   callback_update(stranger, "adm:users:0")):
        seen.clear()
        await dp.feed_update(bot, update)
        assert not any(h.startswith("admin.") for h in seen), f"участник попал в панель: {seen}"

    for update in (callback_update(stranger, "mod:ok:1"), callback_update(stranger, "mod:rej:1")):
        seen.clear()
        await dp.feed_update(bot, update)
        assert not any(h.startswith("moderation.") for h in seen), f"участник модерирует заявки: {seen}"

    for staff in sorted(settings.admin_ids | settings.pc_ids):
        seen.clear()
        await dp.feed_update(bot, message_update(staff, "/admin"))
        assert seen == ["admin.cmd_admin"], f"сотрудник {staff} не попал в панель: {seen}"
        for data, expected in (("mod:ok:1", "moderation.cb_mod_approve"),
                               ("mod:rej:1", "moderation.cb_mod_reject"),
                               ("adm:ok:1", "admin.cb_approve"),
                               ("adm:rej:1", "admin.cb_reject")):
            seen.clear()
            await dp.feed_update(bot, callback_update(staff, data))
            assert seen == [expected], f"сотруднику {staff} недоступно {data}: {seen}"

    for handler, callback in originals:
        object.__setattr__(handler, "callback", callback)
    await bot.session.close()
    print("admin router ok")


async def check_manual_results() -> None:
    """Ручная корректировка (/addresult): баллы, отмена зачёта, обнуление — и доступ только у владельца."""
    owner = sorted(settings.admin_ids)[0]
    pc_id = sorted(settings.pc_ids)[0]
    async with SessionLocal() as s:
        for w in (1, 2, 3):
            await services.set_week_open(s, w, True)
        team = await services.create_team(s, None, "Ручная корректировка", "🧪")
        members = []
        for i in range(2):
            u = await services.get_or_create_user(s, 9100 + i, f"manual{i}")
            await services.register_user(s, u, f"Ручной Участник {i}", "IT", "Ташкент")
            await services.approve_user(s, u, owner)
            await services.join_team(s, u, team.id)
            members.append(u)
        await s.commit()

        task = (await services.list_tasks(s, 1))[0]
        sub = await services.start_submission(s, members[0], task, None)
        await fill_steps(s, sub)
        await services.send_for_review(s, sub)
        await s.commit()
        sub = await services.get_submission(s, sub.id)
        await services.review_submission(s, sub, True, owner)
        await s.commit()
        earned = await services.user_points(s, members[0].id)
        assert earned == task.points

        # начисление и списание попадают и в личный, и в командный зачёт
        total = await services.adjust_points(s, members[0], 150, "помощь в организации", owner)
        await s.commit()
        assert total == earned + 150, total
        team_points = next(r["points"] for r in await services.leaderboard(s) if r["team"].id == team.id)
        assert team_points == total, team_points
        total = await services.adjust_points(s, members[0], -50, "поправка", owner)
        await s.commit()
        assert total == earned + 100

        # отмена зачёта: баллы снимаются, отчёт можно переделать
        returned = await services.revoke_review(s, sub, owner, "фото не по условиям")
        await s.commit()
        sub = await services.get_submission(s, sub.id)
        assert returned == task.points and sub.status == SubmissionStatus.rejected
        assert await services.user_points(s, members[0].id) == 100
        redo = await services.start_submission(s, members[0], task, None)
        assert redo.status == SubmissionStatus.draft, "после отмены зачёта отчёт должен открываться заново"
        await services.cancel_submission(s, redo)
        await s.commit()

        info = await services.clear_user_results(s, members[0], owner)
        await s.commit()
        assert info["points"] == 100 and await services.user_points(s, members[0].id) == 0

        team = await services.get_team(s, team.id)
        info = await services.clear_team_results(s, team, owner)
        await s.commit()
        assert info["members"] == 2, info
        assert next(r["points"] for r in await services.leaderboard(s) if r["team"].id == team.id) == 0

        assert await services.recent_points_log(s, 3), "журнал начислений должен вестись"
        for u in members:
            await services.delete_user(s, await services.get_user_by_id(s, u.id))
        await s.delete(await services.get_team(s, team.id))
        for w in (2, 3):
            await services.set_week_open(s, w, False)
        await s.commit()

    # разбор ввода: число и причина одним сообщением, число с пробелом внутри
    from app.bot.handlers.results import _parse_amount

    assert _parse_amount("200") == (200, "")
    assert _parse_amount("200 помощь в организации") == (200, "помощь в организации")
    assert _parse_amount("1 500 подарки") == (1500, "подарки"), "число с пробелом должно читаться целиком"
    assert _parse_amount("abc") == (0, "")

    # доступ: только владелец, P&C и участники — мимо
    from app.bot.handlers.results import IsOwner

    class Event:
        def __init__(self, uid): self.from_user = type("U", (), {"id": uid})()

    assert await IsOwner()(Event(owner)), "владелец должен иметь доступ"
    assert not await IsOwner()(Event(pc_id)), "у P&C доступа к корректировке быть не должно"
    assert not await IsOwner()(Event(555)), "у участника доступа быть не должно"
    print("manual results ok")


async def check_report_edge_cases() -> None:
    """Два случая, на которых участник терял работу: перезапуск и альбом фото."""
    import datetime

    from aiogram import Bot
    from aiogram.types import CallbackQuery, Chat, Message, PhotoSize, Update
    from aiogram.types import User as TgUser

    uid = 8484
    async with SessionLocal() as s:
        u = await services.get_or_create_user(s, uid, "edge")
        await services.register_user(s, u, "Крайний Случай", "IT", "Ташкент", phone="+998901234567")
        await services.approve_user(s, u, sorted(settings.admin_ids)[0])
        await s.commit()
        # Нужно задание минимум с двумя фото-шагами: такие есть не в каждой неделе,
        # поэтому недели открываем все и в конце возвращаем как было.
        for w in (1, 2, 3):
            await services.set_week_open(s, w, True)
        await s.commit()
        photo_tasks = [t for t in await services.list_tasks(s) if len([x for x in t.steps if x["kind"] != "note"]) >= 2]
        assert photo_tasks, "нужно задание хотя бы с двумя фото-шагами"
        task = photo_tasks[0]
        task_id = task.id

    class Session:
        def __init__(self, bot): self.n = 0
        async def __call__(self, bot, method, timeout=None):
            self.n += 1
            if type(method).__name__ in ("SendMessage", "EditMessageText"):
                return Message(message_id=self.n, date=datetime.datetime.now(),
                               chat=Chat(id=uid, type="private"),
                               from_user=TgUser(id=1, is_bot=True, first_name="b"), text="ok")
            return True
        async def close(self): pass

    def photo_update(group, n):
        return Update(update_id=n, message=Message(
            message_id=200 + n, date=datetime.datetime.now(), chat=Chat(id=uid, type="private"),
            from_user=TgUser(id=uid, is_bot=False, first_name="U"), media_group_id=group,
            photo=[PhotoSize(file_id=f"f{n}", file_unique_id=f"f{n}", width=10, height=10)]))

    def start_update():
        return Update(update_id=1, callback_query=CallbackQuery(
            id="1", from_user=TgUser(id=uid, is_bot=False, first_name="U"), chat_instance="1",
            data=f"sub:start:{task_id}:0",
            message=Message(message_id=1, date=datetime.datetime.now(), chat=Chat(id=uid, type="private"),
                            from_user=TgUser(id=uid, is_bot=False, first_name="U"), text="x")))

    bot = Bot("123:test-token")
    bot.session = Session(bot)
    dp, _ = build_dispatcher()

    # 1. Черновик есть в базе, состояние диалога потеряно (перезапуск) — файл не должен пропасть.
    async with SessionLocal() as s:
        user = await services.get_user(s, uid)
        t = await services.get_task(s, task_id)
        sub = await services.start_submission(s, user, t, None)
        await s.commit()
        sub_id = sub.id
    await dp.feed_update(bot, photo_update(None, 1))
    async with SessionLocal() as s:
        sub = await services.get_submission(s, sub_id)
        assert len(sub.files or []) == 1, "файл после перезапуска потерян"

    # 2. Альбом: в шаг попадает ровно одно фото, остальные не занимают следующие шаги.
    await dp.feed_update(bot, start_update())
    for i in range(3):
        await dp.feed_update(bot, photo_update("ALBUM", 10 + i))
    async with SessionLocal() as s:
        sub = await services.get_submission(s, sub_id)
        from collections import Counter
        per_step = Counter(f.get("step") for f in (sub.files or []))
        assert all(n == 1 for n in per_step.values()), f"альбом разложился по шагам неверно: {per_step}"
        user = await services.get_user(s, uid)
        await services.delete_user(s, user)
        for w in (2, 3):
            await services.set_week_open(s, w, False)
        await s.commit()
    await bot.session.close()
    print("report edge cases ok")


async def check_missing_user_buttons() -> None:
    """Кнопки в старых сообщениях про удалённого участника не должны ронять панель."""
    from app.bot.handlers import admin as adm

    async with SessionLocal() as s:
        ghost = await services.get_or_create_user(s, 9999, "ghost")
        await s.commit()
        ghost_id = ghost.id
        await services.delete_user(s, ghost)
        await s.commit()
        assert await services.get_user_by_id(s, ghost_id) is None

    answered: list[str] = []

    class FakeCQ:
        def __init__(self, data): self.data = data; self.from_user = type("U", (), {"id": 1})()
        message = None
        async def answer(self, text=None, show_alert=False): answered.append(text or "")

    class FakeState:
        async def clear(self): pass
        async def set_state(self, *a): raise AssertionError("не должно дойти до смены состояния")
        async def update_data(self, *a, **k): raise AssertionError("не должно дойти до записи состояния")

    for data, handler in ((f"adm:dq:{ghost_id}", adm.cb_dq), (f"adm:del:{ghost_id}", adm.cb_user_delete)):
        answered.clear()
        await handler(FakeCQ(data), FakeState())
        assert answered and "не найден" in answered[0].lower(), (data, answered)
    print("missing user buttons ok")


async def check_pc_can_moderate() -> None:
    """Сотрудник P&C принимает заявки и проверяет отчёты наравне с админом."""
    pc_id = sorted(settings.pc_ids)[0]
    async with SessionLocal() as s:
        pc = await services.get_or_create_user(s, pc_id, "pc")
        await s.commit()
        assert pc.is_pc, "сотрудник из PC_IDS должен быть отмечен в базе"
        assert pc_id in await services.list_pc_tg_ids(s), "заявки и вопросы должны приходить P&C"

        u = await services.get_or_create_user(s, 7070, "moderated")
        await services.register_user(s, u, "Проверяемый Участник", "IT", "Ташкент")
        await s.commit()
        assert u.status == UserStatus.pending
        await services.approve_user(s, u, pc_id)      # «Принять» из канала заявок
        await s.commit()
        assert u.status == UserStatus.registered and u.moderated_by == pc_id

        task = (await services.list_tasks(s, 1))[0]
        sub = await services.start_submission(s, u, task, None)
        await fill_steps(s, sub)
        await services.send_for_review(s, sub)
        await s.commit()
        sub = await services.get_submission(s, sub.id)
        await services.review_submission(s, sub, True, pc_id)   # «Зачесть» из канала результатов
        await s.commit()
        sub = await services.get_submission(s, sub.id)
        assert sub.status == SubmissionStatus.approved and sub.reviewed_by == pc_id
        assert await services.user_points(s, u.id) == sub.points_awarded > 0

        sub2 = await services.start_submission(s, u, (await services.list_tasks(s, 1))[1], None)
        await fill_steps(s, sub2)
        await services.send_for_review(s, sub2)
        await s.commit()
        sub2 = await services.get_submission(s, sub2.id)
        await services.review_submission(s, sub2, False, pc_id, "нужно фото передачи")
        await s.commit()
        sub2 = await services.get_submission(s, sub2.id)
        assert sub2.status == SubmissionStatus.rejected and sub2.points_awarded == 0

        await services.delete_user(s, u)
        await s.commit()
    print("P&C moderation ok")


async def check_admin_flag_reset() -> None:
    """Флаг админа, оставшийся в базе от прежних настроек, снимается при следующем входе."""
    async with SessionLocal() as s:
        u = await services.get_or_create_user(s, 6161, "stale")
        u.is_admin = True
        u.is_pc = True
        await s.commit()
        u = await services.get_or_create_user(s, 6161, "stale")
        await s.commit()
        assert not u.is_admin and not u.is_pc, "лишние права должны сниматься сами"
        # то же самое при старте бота — даже если участник больше не заходит
        u.is_admin = True
        await s.commit()
        assert await services.sync_staff_flags(s) == 1
        await s.commit()
        assert not (await services.get_user(s, 6161)).is_admin
        await services.delete_user(s, u)
        await s.commit()
    print("admin access ok")


async def check_user_delete() -> None:
    """Админ может удалить участника полностью — тот регистрируется заново с нуля."""
    async with SessionLocal() as s:
        u = await services.get_or_create_user(s, 5151, "todelete")
        await services.register_user(s, u, "Удаляемый Участник", "IT", "Алматы")
        await services.approve_user(s, u, 999)
        rows = await services.leaderboard(s)
        free = next((r["team"] for r in rows if len(r["members"]) < settings.team_size), None)
        if free is not None:
            await services.join_team(s, u, free.id)
        await s.commit()
        task = (await services.list_tasks(s, 1))[0]
        sub = await services.start_submission(s, u, task, None)
        await fill_steps(s, sub)
        await services.send_for_review(s, sub)
        await s.commit()
        sub = await services.get_submission(s, sub.id)
        await services.review_submission(s, sub, True, 999)
        await s.commit()
        assert await services.user_points(s, u.id) > 0
        info = await services.delete_user(s, u)
        await s.commit()
        assert info["submissions"] == 1 and info["points"] > 0, info

    async with SessionLocal() as s:  # свежая сессия: в базе не осталось ни следа
        assert await services.get_user(s, 5151) is None
        again = await services.get_or_create_user(s, 5151, "todelete")
        await services.register_user(s, again, "Удаляемый Участник", "IT", "Алматы")
        await s.commit()
        assert again.status == UserStatus.pending and await services.user_points(s, again.id) == 0
        assert not await services.user_submissions(s, again.id)
        await services.delete_user(s, again)
        await s.commit()
    print("user delete ok")


async def check_registration_resume() -> None:
    """Анкета должна переживать перезапуск бота: шаги сохраняются в базе."""
    async with SessionLocal() as s:
        u = await services.get_or_create_user(s, 4242, "resume")
        assert services.draft_step(u) == "full_name"
        await services.save_draft(s, u, full_name="Анна Восстановленная")
        await s.commit()
        assert services.draft_step(u) == "phone", "после имени ждём телефон"
        await services.save_draft(s, u, phone="+77001234567")
        await s.commit()
        assert services.draft_step(u) == "team"
        assert services.draft_from_user(u) == {"full_name": "Анна Восстановленная", "phone": "+77001234567"}
    print("registration resume ok")


async def check_week_switch() -> None:
    """Недели открывает админ. Пока ни одна не открыта, участник не видит и не сдаёт задания."""
    async with SessionLocal() as s:
        for w in (1, 2, 3):
            await services.set_week_open(s, w, False)
        await s.commit()
        await services.load_open_weeks(s)
        assert services.open_weeks() == [], "выключенные недели не открыты"
        assert services.current_week() is None
        u = await services.get_or_create_user(s, 777, "closedweek")
        await services.register_user(s, u, "Пётр Закрытов", "IT", "Алматы")
        await services.approve_user(s, u, 999)
        await s.commit()
        task = (await services.list_tasks(s, 1))[0]
        assert not services.task_is_open(task)
        try:
            await services.start_submission(s, u, task, None)
            raise AssertionError("отчёт принят при закрытой неделе")
        except services.ServiceError as ex:
            assert "закрыт" in str(ex), ex

        await services.set_week_open(s, 1, True)
        await s.commit()
        assert services.open_weeks() == [1] and services.current_week().number == 1
        assert services.task_is_open(task)
        # отчёт принимается без команды: баллы попадут в командный зачёт после назначения
        assert u.team_id is None
        sub = await services.start_submission(s, u, task, None)
        await s.commit()
        assert sub is not None

        await services.set_week_open(s, 1, False)
        await s.commit()
        assert services.open_weeks() == [] and not services.task_is_open(task)
        await services.set_week_open(s, 1, True)   # вернуть как было для остальных проверок
        await s.commit()
    print("week switch ok")


async def check_auto_migration() -> None:
    """A release that adds a field must upgrade a database made by the previous release.

    Without it the bot crashes at startup with UndefinedColumn — which is exactly what happened
    on the first deploy after the sign-up fields were added.
    """
    from app.db import engine, _add_missing_columns
    from sqlalchemy import inspect, text

    async with engine.begin() as conn:
        await conn.execute(text("ALTER TABLE users DROP COLUMN phone"))
        await conn.execute(text("ALTER TABLE users DROP COLUMN is_pc"))
        # JSON-столбец со значением по умолчанию `list` — отдельный случай, он уже ломал деплой
        await conn.execute(text("ALTER TABLE tasks DROP COLUMN steps"))
        await conn.run_sync(_add_missing_columns)
        cols = await conn.run_sync(lambda c: {x["name"] for x in inspect(c).get_columns("users")})
        task_cols = await conn.run_sync(lambda c: {x["name"] for x in inspect(c).get_columns("tasks")})
    assert {"phone", "is_pc"} <= cols, cols
    assert "steps" in task_cols, task_cols
    async with SessionLocal() as s:
        from app.db import seed_tasks
        await seed_tasks()
        assert services.submission_steps.__module__  # шаги читаются из данных
        task = (await services.list_tasks(s, 1))[0]
        assert task.steps, "шаги задания должны восстановиться из data/tasks.json"
    async with SessionLocal() as s:
        await services.get_or_create_user(s, 424242, "migrated")
        await s.commit()
    print("auto-migration ok")


async def main() -> None:
    print("backend:", settings.database_url.split("://")[0])
    await reset_schema()
    await init_db()
    await check_auto_migration()
    check_no_plain_emoji()
    async with SessionLocal() as s:
        # Недели открывает админ; для остальных проверок открываем первую.
        await services.set_week_open(s, 1, True)
        await s.commit()
    async with SessionLocal() as s:
        tasks = await services.list_tasks(s)
        assert len(tasks) == 12, len(tasks)
        assert {t.week for t in tasks} == {1, 2, 3}
        assert all(len([t for t in tasks if t.week == w]) == 4 for w in (1, 2, 3))
        opt_tasks = [t for t in tasks if t.options]
        assert {t.code for t in opt_tasks} == {6, 9}
        print("tasks seeded:", [(t.week, t.code, t.points_label) for t in tasks])

        # settings: defaults, override, cache invalidation
        assert await services.get_flag(s, "moderation_required") is True
        await services.set_setting(s, "reg_channel_id", "-1001234567890")
        await s.commit()
        assert await services.get_channel_id(s, "reg_channel_id") == -1001234567890
        services.invalidate_settings_cache()
        assert await services.get_channel_id(s, "results_channel_id") is None
        print("settings ok")

        # registration goes through moderation
        users = []
        for i in range(7):
            u = await services.get_or_create_user(s, 1000 + i, f"user{i}")
            status = await services.register_user(s, u, f"Сотрудник {i}", "Отдел", "Алматы")
            assert status == UserStatus.pending, status
            users.append(u)
        await s.commit()
        assert await services.pending_users_count(s) == 7

        # a pending applicant cannot join teams or start reports
        pending_user = users[0]
        try:
            services.ensure_approved(pending_user)
            raise AssertionError("pending user passed the gate")
        except services.ServiceError as ex:
            print("ok gate pending:", ex)

        # approve six, reject one
        for u in users[:6]:
            await services.approve_user(s, u, 999)
        await services.reject_user(s, users[6], 999, "Не сотрудник компании")
        await s.commit()
        assert await services.pending_users_count(s) == 0
        assert users[6].status == UserStatus.rejected and users[6].reject_reason
        try:
            services.ensure_approved(users[6])
            raise AssertionError("rejected user passed the gate")
        except services.ServiceError as ex:
            print("ok gate rejected:", ex)
        # a rejected applicant can re-apply
        await services.register_user(s, users[6], "Сотрудник 6", "Отдел", "Алматы")
        await services.approve_user(s, users[6], 999)
        await s.commit()
        print("moderation ok")

        admin = await services.get_or_create_user(s, 999, "pc_admin")
        assert admin.is_admin

        # teams: 5 max, unique names
        # P&C creates the team empty, then puts people in it — participants no longer join themselves.
        team = await services.create_team(s, users[0], "Добряки", "🔥")
        for u in users[0:5]:
            await services.join_team(s, u, team.id)
        try:
            await services.join_team(s, users[5], team.id)
            raise AssertionError("team overflow allowed")
        except services.ServiceError as ex:
            print("ok team full:", ex)
        try:
            await services.create_team(s, users[5], "добряки")
            raise AssertionError("duplicate team allowed")
        except services.ServiceError as ex:
            print("ok dup name:", ex)
        team2 = await services.create_team(s, users[5], "Лучики", "🌟")
        await services.join_team(s, users[5], team2.id)
        await services.join_team(s, users[6], team2.id)
        await s.commit()

        # submissions: week 1 open, week 2 closed
        u0 = await services.get_user(s, 1000)
        t_w1 = next(t for t in tasks if t.week == 1 and not t.options)
        t_w2 = next(t for t in tasks if t.week == 2)
        t_opt = next(t for t in tasks if t.code == 9)
        try:
            await services.start_submission(s, u0, t_w2, None)
            raise AssertionError("closed week allowed")
        except services.ServiceError as ex:
            print("ok closed week:", ex)
        try:
            await services.start_submission(s, u0, t_opt, None)
            raise AssertionError("option task without option allowed")
        except services.ServiceError as ex:
            print("ok option required:", ex)

        sub = await services.start_submission(s, u0, t_w1, None)
        try:
            await services.send_for_review(s, sub)
            raise AssertionError("empty submission accepted")
        except services.ServiceError as ex:
            print("ok incomplete:", ex)
        # проходим шаги мастера так же, как участник в чате
        await fill_steps(s, sub)
        assert services.first_unfinished_step(sub) == len(services.submission_steps(sub))
        await services.send_for_review(s, sub)
        await s.commit()
        assert sub.status == SubmissionStatus.pending

        # option task: only one option, correct points
        sub2 = await services.start_submission(s, u0, t_opt, t_opt.options[1].id)  # donate = 200
        await fill_steps(s, sub2)
        await services.send_for_review(s, sub2)
        await s.commit()
        try:
            await services.start_submission(s, u0, t_opt, t_opt.options[0].id)
            raise AssertionError("second option allowed")
        except services.ServiceError as ex:
            print("ok single option:", ex)

        # review
        assert await services.pending_count(s) == 2
        sub = await services.get_submission(s, sub.id)
        await services.review_submission(s, sub, True, 999)
        sub2 = await services.get_submission(s, sub2.id)
        await services.review_submission(s, sub2, False, 999, "нет чека")
        await s.commit()
        assert await services.user_points(s, u0.id) == t_w1.points
        board = await services.leaderboard(s)
        assert board[0]["team"].id == team.id and board[0]["points"] == t_w1.points
        print("points ok:", t_w1.points)

        # resubmit after rejection
        sub2 = await services.start_submission(s, u0, t_opt, t_opt.options[0].id)
        assert sub2.status == SubmissionStatus.draft and sub2.option.points == 500

        # disqualification removes points from the team
        await services.disqualify(s, u0, "нарушение", 999)
        await s.commit()
        board = await services.leaderboard(s)
        assert board[0]["points"] == 0 or board[0]["team"].id != team.id
        print("ok disqualification excludes points")
        await services.reinstate(s, u0, 999)
        await s.commit()

        idle = await services.users_without_submissions(s, 1)
        assert u0.id not in {u.id for u in idle} and len(idle) == 6

        # broadcast segments
        all_users = await services.segment_users(s, "all")
        assert len(all_users) == 7, len(all_users)
        assert len(await services.segment_users(s, "no_team")) == 0
        no_reports = await services.segment_users(s, "no_reports_week")
        assert u0.id not in {u.id for u in no_reports} and len(no_reports) == 6
        assert len(await services.segment_users(s, "lt_n_week", "2")) == 7  # u0 sent 1 < 2
        assert len(await services.segment_users(s, "lt_n_week", "1")) == 6
        assert len(await services.segment_users(s, "no_approved_all")) == 6
        assert len(await services.segment_users(s, "pending_approval")) == 0
        assert len(await services.segment_users(s, "disqualified")) == 0
        team_seg = await services.segment_users(s, "team", str(team.id))
        assert len(team_seg) == 5, len(team_seg)
        # u0's rejected report was already restarted as a draft, so nobody is "rejected and not redone"
        assert await services.segment_users(s, "rejected_week") == []
        sub3 = await services.start_submission(s, users[1], t_w1, None)
        await fill_steps(s, sub3)
        await services.send_for_review(s, sub3)
        await s.commit()
        sub3 = await services.get_submission(s, sub3.id)
        await services.review_submission(s, sub3, False, 999, "нет фото")
        await s.commit()
        rejected_seg = await services.segment_users(s, "rejected_week")
        assert {u.id for u in rejected_seg} == {users[1].id}, rejected_seg
        assert services.segment_label("lt_n_week", "3") == "Меньше 3 заданий за неделю"
        print("segments ok:", {c: len(await services.segment_users(s, c)) for c, _, _ in services.SEGMENTS if c not in ("lt_n_week", "team")})
        await export_xlsx(s, __import__("pathlib").Path("data/smoke_export.xlsx"))
        print("export ok")

    # The TestClient runs the app in its own event loop, and asyncpg connections belong to the loop that
    # opened them — release the pool so the client opens fresh ones.
    await engine.dispose()

    await check_week_switch()
    await check_registration_resume()
    await check_user_delete()
    check_phone_parsing()
    check_admin_access()
    await check_admin_flag_reset()
    await check_admin_router_blocks()
    await check_pc_can_moderate()
    await check_missing_user_buttons()
    await check_report_edge_cases()
    await check_manual_results()

    # Служебный HTTP: хостинг проверяет живость этим адресом, интерфейса больше нет.
    client = TestClient(app)
    health = client.get("/api/health").json()
    assert health["ok"] and health["week"] == 1, health
    assert client.get("/").status_code == 200
    assert client.get("/api/bootstrap").status_code == 404, "мини-приложение должно быть удалено"
    print("http ok:", health)

    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
