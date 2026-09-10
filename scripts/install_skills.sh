#!/usr/bin/env bash
# Установить переносимые навыки глобально: они начнут действовать во всех проектах
# этого компьютера, а не только в этом репозитории.
#
#   bash scripts/install_skills.sh            # установить в ~/.claude/skills
#   bash scripts/install_skills.sh /путь      # или в чужой репозиторий: /путь/.claude/skills
#
# Навык dobrik-style не копируется: он про этот проект, а не про боты вообще.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.claude/skills"
if [ $# -ge 1 ]; then
  DEST="$1/.claude/skills"
else
  DEST="${HOME}/.claude/skills"
fi

mkdir -p "$DEST"
for skill in agent-workflow tg-bot-style tg-bot-kit; do
  rm -rf "${DEST:?}/${skill}"
  cp -r "$SRC/$skill" "$DEST/$skill"
  echo "  ✓ $skill → $DEST/$skill"
done

echo
echo "Готово. Навыки подключаются автоматически, когда задача им соответствует."
echo "Проверить: claude → /skills (или спросить «какие навыки доступны»)."
