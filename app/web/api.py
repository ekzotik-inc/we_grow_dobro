"""Telegram Web App backend: validates initData and serves JSON for the mini app."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .. import services, texts
from ..config import settings
from ..db import SessionLocal
from ..models import SubmissionStatus, UserStatus

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="We Grow Dobro — marathon web app", docs_url=None, redoc_url=None)


def validate_init_data(init_data: str, max_age: int = 86400) -> dict:
    """Verify Telegram WebApp initData signature (https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app)."""
    if not init_data:
        raise HTTPException(401, "no initData")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise HTTPException(401, "no hash")
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received_hash):
        raise HTTPException(401, "bad signature")
    auth_date = int(pairs.get("auth_date", "0"))
    if max_age and time.time() - auth_date > max_age:
        raise HTTPException(401, "initData expired")
    try:
        return json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError as ex:
        raise HTTPException(401, "bad user") from ex


async def current_tg_user(authorization: str = Header(default="")) -> dict:
    # header: "tma <initData>"
    scheme, _, data = authorization.partition(" ")
    if scheme.lower() != "tma":
        raise HTTPException(401, "use 'Authorization: tma <initData>'")
    return validate_init_data(data)


def _sub_json(x) -> dict:
    return {
        "id": x.id,
        "task_id": x.task_id,
        "week": x.week,
        "status": x.status.value,
        "status_label": texts.STATUS_LABEL[x.status],
        "option": x.option.title if x.option else None,
        "points": x.points_awarded,
        "target_points": x.target_points,
        "files": len(x.files or []),
        "note": x.note,
        "review_comment": x.review_comment,
        "submitted_at": x.submitted_at.isoformat() if x.submitted_at else None,
    }


@app.get("/api/bootstrap")
async def bootstrap(tg=Depends(current_tg_user)):
    """Everything the mini app needs in one call."""
    async with SessionLocal() as s:
        user = await services.get_or_create_user(s, tg["id"], tg.get("username"))
        await s.commit()
        user = await services.get_user(s, tg["id"])
        my_points = await services.user_points(s, user.id)
        board = await services.leaderboard(s)
        upts = await services.user_points_map(s)
        subs = {x.task_id: x for x in await services.user_submissions(s, user.id) if x.status != SubmissionStatus.cancelled}
        tasks = await services.list_tasks(s)
        cw = settings.current_week()

        team = None
        rank = None
        if user.team_id:
            for i, r in enumerate(board, 1):
                if r["team"].id == user.team_id:
                    rank = i
                    team = {
                        "id": r["team"].id,
                        "name": r["team"].name,
                        "emoji": r["team"].emoji,
                        "points": r["points"],
                        "rank": i,
                        "members": [{"name": m.display_name, "points": upts.get(m.id, 0), "captain": r["team"].captain_id == m.id} for m in r["members"]],
                    }

        return {
            "marathon": {
                "title": settings.marathon_title,
                "status": settings.marathon_status(),
                "current_week": cw.number if cw else None,
                "team_size": settings.team_size,
                "weeks": [{"number": w.number, "start": w.start.isoformat(), "end": w.end.isoformat(), "label": w.label} for w in settings.weeks],
                "pc_contact": settings.pc_contact,
            },
            "me": {
                "name": user.display_name,
                "department": user.department,
                "status": user.status.value,
                "registered": user.status != UserStatus.new,
                "points": my_points,
                "is_admin": user.is_admin,
                "team": team,
                "rank": rank,
            },
            "tasks": [
                {
                    "id": t.id,
                    "code": t.code,
                    "week": t.week,
                    "title": t.title,
                    "emoji": t.emoji,
                    "description": t.description,
                    "conditions": t.conditions,
                    "points": t.points,
                    "points_label": t.points_label,
                    "min_photos": t.min_photos,
                    "note_required": t.note_required,
                    "open": services.task_is_open(t),
                    "options": [{"id": o.id, "title": o.title, "points": o.points, "conditions": o.conditions, "min_photos": o.min_photos} for o in t.options],
                    "submission": _sub_json(subs[t.id]) if t.id in subs else None,
                }
                for t in tasks
            ],
            "leaderboard": [
                {
                    "id": r["team"].id,
                    "name": r["team"].name,
                    "emoji": r["team"].emoji,
                    "points": r["points"],
                    "members": len(r["members"]),
                    "mine": r["team"].id == user.team_id,
                }
                for r in board
            ],
        }


@app.get("/api/health")
async def health():
    return {"ok": True, "week": settings.current_week().number if settings.current_week() else None}


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")
