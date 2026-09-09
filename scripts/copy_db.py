"""Перенос данных между базами: из Neon в Render (или в любую другую).

Копирует все таблицы проекта в правильном порядке и сбрасывает счётчики id.
Работает между любыми поддерживаемыми базами, потому что читает и пишет через модели,
а не через дамп: несовпадение версий PostgreSQL значения не имеет.

    python -m scripts.copy_db --from "<строка старой базы>" --to "<строка новой базы>"

По умолчанию новая база должна быть пустой: так исключён случайный «двойной» перенос.
Ключ --force разрешает писать в непустую (существующие строки будут удалены).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete, func, select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.config import _normalize_db_url  # noqa: E402
from app.models import (  # noqa: E402
    AppSetting, Base, Broadcast, PointsLog, Submission, Task, TaskOption, Team, User,
)

# Порядок важен: сначала то, на что ссылаются остальные.
ORDER = [Team, User, Task, TaskOption, Submission, PointsLog, AppSetting, Broadcast]


def _engine(url: str):
    url = _normalize_db_url(url)
    kwargs = {} if url.startswith("sqlite") else {"pool_pre_ping": True}
    return create_async_engine(url, **kwargs)


async def copy(src_url: str, dst_url: str, force: bool) -> None:
    src, dst = _engine(src_url), _engine(dst_url)
    SrcSession = async_sessionmaker(src, expire_on_commit=False)
    DstSession = async_sessionmaker(dst, expire_on_commit=False)

    async with dst.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with DstSession() as d:
        existing = {}
        for model in ORDER:
            n = (await d.execute(select(func.count()).select_from(model))).scalar_one()
            if n:
                existing[model.__tablename__] = n
        if existing and not force:
            raise SystemExit(f"Новая база не пуста: {existing}. Перенос отменён (--force, чтобы перезаписать).")
        if existing:
            for model in reversed(ORDER):
                await d.execute(delete(model))
            await d.commit()

    totals = {}
    async with SrcSession() as s, DstSession() as d:
        for model in ORDER:
            rows = list((await s.execute(select(model))).scalars())
            for row in rows:
                values = {c.name: getattr(row, c.name) for c in model.__table__.columns}
                await d.execute(model.__table__.insert().values(**values))
            totals[model.__tablename__] = len(rows)
            await d.commit()

    # После вставки с явными id счётчики PostgreSQL остаются на нуле — поправим.
    if not dst.url.drivername.startswith("sqlite"):
        async with dst.begin() as conn:
            for model in ORDER:
                table = model.__table__
                if "id" not in table.c:
                    continue
                from sqlalchemy import text

                await conn.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table.name}), 1), true)"
                ))

    await src.dispose()
    await dst.dispose()
    print("Перенесено строк:")
    for name, n in totals.items():
        print(f"  {name}: {n}")


def main() -> None:
    p = argparse.ArgumentParser(description="Перенос данных между базами проекта")
    p.add_argument("--from", dest="src", required=True, help="строка подключения старой базы")
    p.add_argument("--to", dest="dst", required=True, help="строка подключения новой базы")
    p.add_argument("--force", action="store_true", help="разрешить запись в непустую базу")
    args = p.parse_args()
    asyncio.run(copy(args.src, args.dst, args.force))


if __name__ == "__main__":
    main()
