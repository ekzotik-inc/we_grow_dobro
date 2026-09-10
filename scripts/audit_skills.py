"""Проверка навыков проекта: их точно подхватит новый проект.

Смотрим формально проверяемое: заголовок на месте, имя совпадает с папкой, описание
не пустое и в пределах лимита, все упомянутые файлы-справочники существуют,
а переносимые навыки не привязаны к этому проекту.

Запуск: python -m scripts.audit_skills
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / ".claude" / "skills"
# Навыки, которые копируются в любой следующий проект.
PORTABLE = {"agent-workflow", "tg-bot-style", "tg-bot-kit"}
# Слова этого проекта: в переносимом навыке они могут быть только примером в кавычках.
PROJECT_WORDS = ("Добрик", "марафон", "Марафон", "P&C", "добрых дел")

problems: list[str] = []


def check_skill(folder: Path) -> None:
    name = folder.name
    skill = folder / "SKILL.md"
    if not skill.exists():
        problems.append(f"{name}: нет SKILL.md")
        return
    text = skill.read_text(encoding="utf-8")
    head = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not head:
        problems.append(f"{name}: нет заголовка --- name/description ---")
        return
    meta = dict(re.findall(r"^(name|description):\s*(.+)$", head.group(1), re.M))
    if meta.get("name") != name:
        problems.append(f"{name}: поле name = {meta.get('name')!r}, а папка называется иначе")
    desc = meta.get("description", "")
    if len(desc) < 40:
        problems.append(f"{name}: описание слишком короткое — по нему решают, подключать ли навык")
    if len(desc) > 1024:
        problems.append(f"{name}: описание длиннее 1024 символов")
    if "спользовать" not in desc:
        problems.append(f"{name}: в описании не сказано, КОГДА применять навык")

    # Все справочники, на которые ссылается навык, должны существовать.
    for ref in set(re.findall(r"`(references/[\w./-]+\.md)`", text)):
        if not (folder / ref).exists():
            problems.append(f"{name}: ссылается на {ref}, а файла нет")
    for ref in (folder / "references").glob("*.md") if (folder / "references").exists() else []:
        if f"references/{ref.name}" not in text:
            problems.append(f"{name}: файл references/{ref.name} не упомянут в SKILL.md")

    if name in PORTABLE:
        # Проверяем и сам навык, и все его справочники: копироваться будет папка целиком.
        for path in [skill, *sorted((folder / "references").glob("*.md"))]:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith(">"):
                    continue
                for word in PROJECT_WORDS:
                    if word in line and "«" not in line and '"' not in line and "`" not in line:
                        problems.append(f"{name}/{path.name}: строка привязана к этому проекту "
                                        f"→ {line.strip()[:70]}")
                        break

    print(f"  ✓ {name}: {len(text.splitlines())} строк, "
          f"{len(list((folder / 'references').glob('*.md'))) if (folder / 'references').exists() else 0} справочник(ов)")


def main() -> None:
    if not SKILLS.exists():
        print("Навыков нет")
        sys.exit(1)
    folders = sorted(p for p in SKILLS.iterdir() if p.is_dir())
    print(f"НАВЫКОВ: {len(folders)}")
    for folder in folders:
        check_skill(folder)

    missing = PORTABLE - {p.name for p in folders}
    if missing:
        problems.append(f"нет переносимых навыков: {', '.join(sorted(missing))}")

    installer = ROOT / "scripts" / "install_skills.sh"
    if not installer.exists():
        problems.append("нет scripts/install_skills.sh — навыки нечем поставить в новый проект")
    else:
        text = installer.read_text(encoding="utf-8")
        for name in PORTABLE:
            if name not in text:
                problems.append(f"install_skills.sh не копирует {name}")

    readme = SKILLS / "README.md"
    if not readme.exists():
        problems.append("нет .claude/skills/README.md — как переносить навыки")

    print(f"\nПРОБЛЕМ: {len(problems)}")
    for p in problems:
        print("  •", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
