"""Проверка готовности к запуску: всё ли на месте перед тем, как звать участников.

    python -m scripts.preflight

Проверяет данные и настройки, до которых можно дотянуться из кода. То, что зависит от
Telegram и хостинга (каналы, тариф), перечисляется отдельным списком для ручной проверки.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", "1:preflight")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app import services, texts  # noqa: E402
from app.bot.common import IMAGES  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.emoji import CHARS  # noqa: E402

problems: list[str] = []
notes: list[str] = []


def check(ok: bool, good: str, bad: str) -> None:
    print(("  ✅ " if ok else "  ❌ ") + (good if ok else bad))
    if not ok:
        problems.append(bad)


async def main() -> None:
    await init_db()
    print("ДАННЫЕ")
    async with SessionLocal() as s:
        tasks = await services.list_all_tasks(s)
        by_week = {w: [t for t in tasks if t.week == w] for w in (1, 2, 3)}
        check(len(tasks) == 12, "12 заданий загружено", f"заданий {len(tasks)}, а должно быть 12")
        check(all(len(v) == 4 for v in by_week.values()), "по 4 задания в каждой неделе",
              f"по неделям: { {k: len(v) for k, v in by_week.items()} }")
        no_steps = [t.code for t in tasks if not t.steps and not any(o.steps for o in t.options)]
        check(not no_steps, "у всех заданий расписаны шаги", f"без шагов: {no_steps}")
        no_image = [t.code for t in tasks if not t.image or not (IMAGES / t.image).exists()]
        check(not no_image, "у всех заданий есть картинка", f"без картинки: {no_image}")
        covers = [w for w in (1, 2, 3) if not (IMAGES / f"week{w}.png").exists()]
        check(not covers, "обложки всех трёх недель на месте", f"нет обложек недель: {covers}")

        open_weeks = await services.load_open_weeks(s)
        if open_weeks:
            notes.append(f"Открыты недели: {sorted(open_weeks)}. Перед стартом обычно открывают только первую.")
        else:
            notes.append("Ни одна неделя не открыта — участники увидят «Задания скоро откроются». "
                         "Откройте первую в /admin → «Недели и задания» в день старта.")

        reg_ch = await services.get_channel_id(s, "reg_channel_id")
        res_ch = await services.get_channel_id(s, "results_channel_id")
        if reg_ch and res_ch:
            print(f"  ✅ каналы подключены: заявки {reg_ch}, результаты {res_ch}")
        else:
            notes.append("Каналы не привязаны — карточки заявок и отчётов пойдут в личку P&C. "
                         "Привязать: /admin → «Каналы и настройки». Чтобы привязка пережила смену базы, "
                         "укажите REG_CHANNEL_ID и RESULTS_CHANNEL_ID в переменных окружения.")

        for flag, human in (("registration_open", "приём заявок"), ("submissions_open", "приём отчётов"),
                            ("moderation_required", "модерация заявок")):
            state = await services.get_flag(s, flag)
            check(state, f"{human}: включено", f"{human} выключено — включите в «Каналы и настройки»")

    print("\nНАСТРОЙКИ")
    check(bool(settings.admin_ids), f"админы: {sorted(settings.admin_ids)}", "ADMIN_IDS пуст — панель будет недоступна")
    check(bool(settings.pc_ids), f"сотрудники P&C: {sorted(settings.pc_ids)}",
          "PC_IDS пуст — заявки и вопросы пойдут админам")
    check(bool(settings.pc_username), f"ссылка на P&C: @{settings.pc_username}",
          "PC_USERNAME не задан — участник не получит прямую ссылку")
    check(settings.team_size == 5, "команда на 5 человек", f"размер команды {settings.team_size}")
    weeks = " · ".join(f"{w.number}: {w.label}" for w in settings.weeks)
    print(f"  ℹ️ недели: {weeks}")
    print(f"  ℹ️ часовой пояс: {settings.tz}")
    print(f"  ℹ️ код страны для телефонов: +{settings.phone_country_code}")
    db = "PostgreSQL" if not settings.database_url.startswith("sqlite") else "SQLite (только для тестов!)"
    print(f"  ℹ️ база: {db}")
    check(len(CHARS) > 500, f"премиум-эмодзи: {len(CHARS)} символов", "набор премиум-эмодзи не загрузился")
    check(bool(texts.RULES) and "Правила марафона" in texts.RULES, "правила на месте", "правила пустые")

    print("\nПРОВЕРИТЬ ВРУЧНУЮ")
    for line in (
        "Тариф Render: сервис не должен засыпать (Settings → Instance Type).",
        "База: платный Postgres на Render, DATABASE_URL подставлен автоматически.",
        "Оба канала подключены и бот в них администратор: /admin → «Каналы и настройки» → «Проверить каналы».",
        "Сотрудник P&C добавлен в оба канала — иначе не увидит карточки.",
        "Команды созданы: /admin → «Команды» → «Создать команду».",
        "В день старта открыть первую неделю: /admin → «Недели и задания».",
    ):
        print(f"  • {line}")

    if notes:
        print("\nОБРАТИТЕ ВНИМАНИЕ")
        for n in notes:
            print(f"  ⚠️ {n}")
    print("\nПРОБЛЕМ:", len(problems))
    sys.exit(1 if problems else 0)


asyncio.run(main())
