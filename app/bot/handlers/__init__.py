from aiogram import Router

from . import admin, moderation, results, start, tasks, teams


def setup_routers() -> Router:
    root = Router(name="root")
    # Admin and moderation first: their deep links (/start modrej_, /start subrej_) must win over /start.
    # Ручная корректировка результатов — до админ-панели: у неё свой, более узкий доступ.
    root.include_router(results.router)
    root.include_router(admin.router)
    root.include_router(moderation.router)
    root.include_router(start.router)
    root.include_router(teams.router)
    root.include_router(tasks.router)
    # Последним: отвечает на всё, что не подошло другим обработчикам, — бот никогда не молчит.
    root.include_router(start.fallback_router)
    return root
