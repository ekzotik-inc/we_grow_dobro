from aiogram import Router

from . import admin, moderation, start, tasks, teams


def setup_routers() -> Router:
    root = Router(name="root")
    # Admin and moderation first: their deep links (/start modrej_, /start subrej_) must win over /start.
    root.include_router(admin.router)
    root.include_router(moderation.router)
    root.include_router(start.router)
    root.include_router(teams.router)
    root.include_router(tasks.router)
    return root
