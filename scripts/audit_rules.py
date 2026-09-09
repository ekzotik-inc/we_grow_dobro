"""Проверка правил марафона на живых данных."""
import sys
import asyncio, os
os.environ.update(BOT_TOKEN="1:x", DATABASE_URL="sqlite+aiosqlite:///:memory:")
sys.path.insert(0, __import__('os').path.dirname(__import__('os').path.dirname(__import__('os').path.abspath(__file__))))
from app.db import init_db, SessionLocal
from app import services
from app.models import SubmissionStatus, UserStatus

ADMIN = 1357560299
bad = []
def check(ok, what):
    print(("  ✓ " if ok else "  ✗ ") + what)
    if not ok: bad.append(what)

async def fill(s, sub, note="ок"):
    for i, st in enumerate(services.submission_steps(sub)):
        if st["kind"] == "note": await services.save_step_answer(s, sub, i, note)
        else: await services.add_file(s, sub, {"type":"photo","file_id":f"f{i}","name":None}, step=i)

async def main():
    await init_db()
    async with SessionLocal() as s:
        for w in (1,2,3): await services.set_week_open(s, w, True)
        users=[]
        for i in range(7):
            u = await services.get_or_create_user(s, 100+i, f"u{i}")
            await services.register_user(s, u, f"Участник {i}", "IT", "Ташкент")
            await services.approve_user(s, u, ADMIN); users.append(u)
        await s.commit()
        print("ПРАВИЛА МАРАФОНА")
        team = await services.create_team(s, None, "Добряки", "🔥")
        for u in users[:5]: await services.join_team(s, u, team.id)
        await s.commit()
        try:
            await services.join_team(s, users[5], team.id); check(False, "в команду больше 5 человек не пускает")
        except services.ServiceError: check(True, "в команду больше 5 человек не пускает")
        try:
            await services.create_team(s, None, "добряки"); check(False, "команды с одинаковым названием запрещены")
        except services.ServiceError: check(True, "команды с одинаковым названием запрещены")

        tasks = await services.list_tasks(s)
        w1 = [t for t in tasks if t.week == 1]
        check(len(w1) == 4 and len(tasks) == 12, "12 заданий, по 4 в неделю")
        opt_task = next(t for t in tasks if t.options)
        try:
            await services.start_submission(s, users[0], opt_task, None); check(False, "у задания с вариантами нельзя без выбора")
        except services.ServiceError: check(True, "у задания с вариантами нельзя без выбора")
        sub = await services.start_submission(s, users[0], opt_task, opt_task.options[0].id)
        await fill(s, sub); await services.send_for_review(s, sub); await s.commit()
        try:
            await services.start_submission(s, users[0], opt_task, opt_task.options[1].id); check(False, "второй вариант того же задания запрещён")
        except services.ServiceError: check(True, "второй вариант того же задания запрещён")

        # неполный отчёт
        t2 = next(t for t in w1 if not t.options and t.id != opt_task.id)
        sub2 = await services.start_submission(s, users[1], t2, None)
        try:
            await services.send_for_review(s, sub2); check(False, "неполный отчёт не отправляется")
        except services.ServiceError as ex: check("шаг" in str(ex), "неполный отчёт не отправляется и называет шаги")

        # баллы только после подтверждения
        await fill(s, sub2); await services.send_for_review(s, sub2); await s.commit()
        check(await services.user_points(s, users[1].id) == 0, "баллы не начисляются до проверки")
        sub2 = await services.get_submission(s, sub2.id)
        await services.review_submission(s, sub2, True, ADMIN); await s.commit()
        check(await services.user_points(s, users[1].id) == t2.points, "баллы приходят после «Зачесть»")

        # закрытая неделя
        await services.set_week_open(s, 2, False); await s.commit()
        t_w2 = next(t for t in tasks if t.week == 2)
        try:
            await services.start_submission(s, users[2], t_w2, None); check(False, "задание закрытой недели не принимается")
        except services.ServiceError: check(True, "задание закрытой недели не принимается")
        check(not services.week_is_open(2) and 2 not in services.visible_weeks(), "закрытая неделя скрыта из списка")

        # дисквалификация
        before = (await services.leaderboard(s))[0]["points"]
        await services.disqualify(s, users[1], "нарушение", ADMIN); await s.commit()
        after = (await services.leaderboard(s))[0]["points"]
        check(after == before - t2.points, "баллы дисквалифицированного уходят из командного зачёта")
        await services.reinstate(s, users[1], ADMIN); await s.commit()
        check((await services.leaderboard(s))[0]["points"] == before, "восстановление возвращает баллы")

        # отмена и повторная отправка
        sub3 = await services.start_submission(s, users[2], w1[2], None)
        await services.cancel_submission(s, sub3); await s.commit()
        sub3b = await services.start_submission(s, users[2], w1[2], None)
        check(sub3b is not None, "после отмены задание можно взять заново")
        # отклонённый отчёт можно переделать
        await fill(s, sub3b); await services.send_for_review(s, sub3b); await s.commit()
        sub3b = await services.get_submission(s, sub3b.id)
        await services.review_submission(s, sub3b, False, ADMIN, "мало фото"); await s.commit()
        again = await services.start_submission(s, users[2], w1[2], None)
        check(again.status == SubmissionStatus.draft, "отклонённый отчёт можно переделать")

        # регистрация
        newbie = await services.get_or_create_user(s, 900, "new")
        check(newbie.status == UserStatus.new, "новый участник не имеет доступа до анкеты")
        try:
            services.ensure_approved(newbie); check(False, "без подтверждения P&C доступа нет")
        except services.ServiceError: check(True, "без подтверждения P&C доступа нет")
    print("ПРОБЛЕМ:", len(bad))
asyncio.run(main())
