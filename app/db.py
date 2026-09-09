from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings
from .models import Base, Task, TaskOption


def _engine_kwargs() -> dict:
    """Tuning for hosted Postgres. SQLite needs none of it.

    - pre-ping/recycle: serverless Postgres (Neon, Supabase) suspends an idle database and drops its
      connections; without pre-ping the bot hands out a dead pooled connection and fails on the next query.
    - statement caches: a "-pooler" host is PgBouncer in transaction mode, where connections are shared
      between transactions. asyncpg's prepared statements do not survive that and raise
      DuplicatePreparedStatementError, so both caches have to be off.
    """
    if settings.database_url.startswith("sqlite"):
        return {}
    kwargs: dict = {"pool_pre_ping": True, "pool_recycle": 300}
    if "-pooler." in settings.database_url:
        # The matching prepared_statement_cache_size=0 is added to the URL in config._normalize_db_url,
        # because SQLAlchemy's asyncpg dialect only reads that one from the URL query.
        kwargs["connect_args"] = {"statement_cache_size": 0}
    return kwargs


engine = create_async_engine(settings.database_url, echo=False, **_engine_kwargs())
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


async def init_db() -> None:
    if settings.database_url.startswith("sqlite"):
        path = settings.database_url.split("///", 1)[-1]
        if path and path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)
    await seed_tasks()


def _column_ddl(dialect, column) -> str | None:
    """`ADD COLUMN` clause for a column the live table is missing, or None if it cannot be added safely."""
    kind = column.type.compile(dialect)
    if column.nullable:
        return f"{column.name} {kind}"
    default = getattr(column.default, "arg", None)
    if column.server_default is not None or callable(default) or default is None:
        return None
    literal = "TRUE" if default is True else "FALSE" if default is False else repr(default)
    return f"{column.name} {kind} NOT NULL DEFAULT {literal}"


def _add_missing_columns(conn) -> None:
    """Bring an existing database up to the current models.

    The project has no migration tool: `create_all` builds missing tables but never touches a table
    that already exists, so a release that adds a field would keep crashing with UndefinedColumn on
    a database created by an earlier release. Only additive changes are applied here — nothing is
    dropped or retyped, so running it against an up-to-date database does nothing.
    """
    inspector = inspect(conn)
    live_tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in live_tables:
            continue
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing:
                continue
            clause = _column_ddl(conn.dialect, column)
            if clause is None:
                log.warning("Столбец %s.%s не добавлен автоматически — нужна ручная миграция.", table.name, column.name)
                continue
            log.info("Добавляю столбец %s.%s", table.name, column.name)
            conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {clause}"))


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
            task.image = item.get("image")
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
