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
from app.db import SessionLocal, engine, init_db  # noqa: E402
from app.export import export_xlsx  # noqa: E402
from app.models import Base, SubmissionStatus, UserStatus  # noqa: E402
from app.web.api import app  # noqa: E402


def init_data_for(user: dict) -> str:
    pairs = {"auth_date": str(int(time.time())), "query_id": "AAE", "user": json.dumps(user, separators=(",", ":"))}
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


async def reset_schema() -> None:
    """Start from an empty database on any backend: deleting a file only works for SQLite."""
    if settings.database_url.startswith("sqlite"):
        path = settings.database_url.split("///")[-1]
        if path and path != ":memory:" and os.path.exists(path):
            os.remove(path)
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


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
        await conn.run_sync(_add_missing_columns)
        cols = await conn.run_sync(lambda c: {x["name"] for x in inspect(c).get_columns("users")})
    assert {"phone", "is_pc"} <= cols, cols
    async with SessionLocal() as s:
        await services.get_or_create_user(s, 424242, "migrated")
        await s.commit()
    print("auto-migration ok")


async def main() -> None:
    print("backend:", settings.database_url.split("://")[0])
    await reset_schema()
    await init_db()
    await check_auto_migration()
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
        for i in range(t_w1.min_photos):
            await services.add_file(s, sub3, {"type": "photo", "file_id": f"z{i}", "name": None})
        await services.set_note(s, sub3, "проба")
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
    # a week's tasks must not leave the server before that week starts
    assert js["marathon"]["current_week"] == 1
    assert {t["week"] for t in js["tasks"]} == {1}, {t["week"] for t in js["tasks"]}
    assert [w["visible"] for w in js["marathon"]["weeks"]] == [True, False, False]
    assert client.get("/").status_code == 200 and client.get("/static/app.js").status_code == 200
    import json, pathlib
    pathlib.Path("/tmp/boot.json").write_text(json.dumps(js, ensure_ascii=False))
    print("web api ok:", js["me"])
    print("\nALL SMOKE TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
