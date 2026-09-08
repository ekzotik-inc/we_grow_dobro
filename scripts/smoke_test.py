"""End-to-end check of the business logic and the Web App API without Telegram.

Run:  BOT_TOKEN=test:token FORCE_WEEK=1 DATABASE_URL=sqlite+aiosqlite:///./data/smoke.db python -m scripts.smoke_test
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from urllib.parse import urlencode

os.environ.setdefault("BOT_TOKEN", "123:test-token")
os.environ.setdefault("FORCE_WEEK", "1")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./data/smoke.db")
os.environ.setdefault("ADMIN_IDS", "999")

from fastapi.testclient import TestClient  # noqa: E402

from app import services  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.export import export_xlsx  # noqa: E402
from app.models import SubmissionStatus  # noqa: E402
from app.web.api import app  # noqa: E402


def init_data_for(user: dict) -> str:
    pairs = {"auth_date": str(int(time.time())), "query_id": "AAE", "user": json.dumps(user, separators=(",", ":"))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


async def main() -> None:
    db = settings.database_url.split("///")[-1]
    if os.path.exists(db):
        os.remove(db)
    await init_db()
    async with SessionLocal() as s:
        tasks = await services.list_tasks(s)
        assert len(tasks) == 12, len(tasks)
        assert {t.week for t in tasks} == {1, 2, 3}
        assert all(len([t for t in tasks if t.week == w]) == 4 for w in (1, 2, 3))
        opt_tasks = [t for t in tasks if t.options]
        assert {t.code for t in opt_tasks} == {6, 9}
        print("tasks seeded:", [(t.week, t.code, t.points_label) for t in tasks])

        # registration
        users = []
        for i in range(7):
            u = await services.get_or_create_user(s, 1000 + i, f"user{i}")
            await services.register_user(s, u, f"Сотрудник {i}", "Отдел", "Алматы")
            users.append(u)
        admin = await services.get_or_create_user(s, 999, "pc_admin")
        assert admin.is_admin

        # teams: 5 max, unique names
        team = await services.create_team(s, users[0], "Добряки", "🔥")
        for u in users[1:5]:
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
        for i in range(t_w1.min_photos):
            await services.add_file(s, sub, {"type": "photo", "file_id": f"f{i}", "name": None})
        await services.set_note(s, sub, "Поблагодарил коллегу за помощь")
        await services.send_for_review(s, sub)
        await s.commit()
        assert sub.status == SubmissionStatus.pending

        # option task: only one option, correct points
        sub2 = await services.start_submission(s, u0, t_opt, t_opt.options[1].id)  # donate = 200
        await services.add_file(s, sub2, {"type": "photo", "file_id": "x", "name": None})
        await services.set_note(s, sub2, "перевод")
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
        await export_xlsx(s, __import__("pathlib").Path("data/smoke_export.xlsx"))
        print("export ok")

    # Web App API
    client = TestClient(app)
    assert client.get("/api/health").json()["ok"]
    assert client.get("/api/bootstrap").status_code == 401
    bad = init_data_for({"id": 1000}).replace("hash=", "hash=00")
    assert client.get("/api/bootstrap", headers={"Authorization": "tma " + bad}).status_code == 401
    r = client.get("/api/bootstrap", headers={"Authorization": "tma " + init_data_for({"id": 1000, "username": "user0", "first_name": "A"})})
    assert r.status_code == 200, r.text
    js = r.json()
    assert js["me"]["team"]["name"] == "Добряки" and js["me"]["points"] == t_w1.points
    assert len(js["tasks"]) == 12 and js["marathon"]["current_week"] == 1
    assert client.get("/").status_code == 200 and client.get("/static/app.js").status_code == 200
    print("web api ok:", js["me"])
    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
