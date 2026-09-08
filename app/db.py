from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings
from .models import Base, Task, TaskOption


def _engine_kwargs() -> dict:
    """Serverless Postgres (Neon, Supabase) suspends an idle database and drops its connections.
    Without pre-ping the bot would hand out a dead pooled connection and fail on the next query."""
    if settings.database_url.startswith("sqlite"):
        return {}
    return {"pool_pre_ping": True, "pool_recycle": 300}


engine = create_async_engine(settings.database_url, echo=False, **_engine_kwargs())
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


async def init_db() -> None:
    if settings.database_url.startswith("sqlite"):
        path = settings.database_url.split("///", 1)[-1]
        if path and path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_tasks()


async def seed_tasks() -> None:
    """Upsert tasks from data/tasks.json (idempotent: keyed by task code / option code)."""
    raw = json.loads((DATA_DIR / "tasks.json").read_text(encoding="utf-8"))
    async with SessionLocal() as s:
        for item in raw:
            task = (await s.execute(select(Task).where(Task.code == item["code"]))).scalar_one_or_none()
            if task is None:
                task = Task(code=item["code"])
                s.add(task)
            task.week = item["week"]
            task.title = item["title"]
            task.emoji = item.get("emoji", "✅")
            task.description = item["description"]
            task.conditions = item["conditions"]
            task.points = item["points"]
            task.min_photos = item.get("min_photos", 1)
            task.note_required = item.get("note_required", True)
            await s.flush()
            existing = {o.code: o for o in (await s.execute(select(TaskOption).where(TaskOption.task_id == task.id))).scalars()}
            for opt in item.get("options", []):
                o = existing.get(opt["code"]) or TaskOption(task_id=task.id, code=opt["code"])
                o.title = opt["title"]
                o.points = opt["points"]
                o.min_photos = opt.get("min_photos", 1)
                o.conditions = opt["conditions"]
                s.add(o)
        await s.commit()
