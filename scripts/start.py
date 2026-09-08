"""Local launcher: checks .env, then starts the bot.

Called by run.bat. All user-facing text lives here rather than in the .bat because Python on
Windows writes to the console through the wide-char API and therefore ignores the console
code page, while a .bat with Cyrillic depends on `chcp` behaving.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Run as `python scripts/start.py`, sys.path[0] is scripts/ — the project root must be importable.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ENV = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
PLACEHOLDER = "123456:ABC"

LINE = "=" * 52


def say(*lines: str) -> None:
    print(*lines, sep="\n", flush=True)


def open_in_editor(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(path)  # noqa: S606  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:  # noqa: BLE001
        say(f"Откройте файл вручную: {path}")


def need_setup(reason: str) -> int:
    say(
        "",
        LINE,
        f"  {reason}",
        LINE,
        "",
        "Сейчас откроется файл .env. Заполните в нём две строки:",
        "",
        "  BOT_TOKEN=   токен вашего бота от @BotFather",
        "  ADMIN_IDS=   ваш Telegram id (узнать: напишите @userinfobot)",
        "",
        "Пример заполненных строк:",
        "  BOT_TOKEN=8123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
        "  ADMIN_IDS=248112233",
        "",
        "Сохраните файл (Ctrl+S), закройте редактор и запустите run.bat снова.",
        "",
    )
    open_in_editor(ENV)
    return 1


def main() -> int:
    os.chdir(ROOT)  # relative paths (data/marathon.db, data/export.xlsx) need the project root
    say(LINE, "  We Grow Dobro — бот марафона добрых дел", LINE, "")

    if not ENV.exists():
        if not ENV_EXAMPLE.exists():
            say("[ОШИБКА] Нет ни .env, ни .env.example — проект скачан не полностью.")
            return 1
        shutil.copyfile(ENV_EXAMPLE, ENV)
        return need_setup("Создан файл настроек .env")

    from dotenv import dotenv_values

    values = dotenv_values(ENV)
    token = (values.get("BOT_TOKEN") or "").strip()
    if not token or token.startswith(PLACEHOLDER):
        return need_setup("В файле .env не заполнен BOT_TOKEN")

    admins = (values.get("ADMIN_IDS") or "").strip()
    if not admins or admins.startswith("111111111"):
        say(
            "[ВНИМАНИЕ] В .env не указан ADMIN_IDS — панель /admin будет недоступна,",
            "           заявки и отчёты проверять будет некому.",
            "           Впишите свой Telegram id (узнать: @userinfobot) и перезапустите.",
            "",
        )

    week = (values.get("FORCE_WEEK") or "0").strip()
    if week and week != "0":
        say(f"[РЕЖИМ ТЕСТА] Принудительно открыта неделя {week} (FORCE_WEEK={week}).",
            "              Перед реальным стартом марафона поставьте FORCE_WEEK=0.", "")

    say("Запускаю бота. Остановить — Ctrl+C в этом окне.", LINE, "")

    import asyncio

    from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError

    from app.main import run

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        say("", "Бот остановлен.")
    except TelegramUnauthorizedError:
        say(
            "",
            LINE,
            "  [ОШИБКА] Telegram не принял токен",
            LINE,
            "",
            "Скорее всего BOT_TOKEN в файле .env скопирован не полностью или с лишним пробелом.",
            "Возьмите токен заново у @BotFather (команда /mybots → ваш бот → API Token)",
            "и вставьте его в .env целиком, без кавычек и пробелов.",
            "",
        )
        return 1
    except TelegramNetworkError as ex:
        say(
            "",
            LINE,
            "  [ОШИБКА] Нет связи с Telegram",
            LINE,
            "",
            "Бот не смог подключиться к api.telegram.org. Возможные причины:",
            "  • нет интернета;",
            "  • Telegram блокируется провайдером — включите VPN и запустите снова;",
            "  • подключение режет корпоративный антивирус или файрвол.",
            "",
            f"Техническая деталь: {ex}",
            "",
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
