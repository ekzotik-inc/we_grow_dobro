from aiogram import Router

from . import admin, start, tasks, teams


def setup_routers() -> Router:
    root = Router(name="root")
    root.include_router(admin.router)
    root.include_router(start.router)
    root.include_router(teams.router)
    root.include_router(tasks.router)
    return root
