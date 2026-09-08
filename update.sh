#!/usr/bin/env bash
# Обновление бота: забрать свежий код, доставить зависимости, перезапустить службу.
# Запуск:  bash update.sh
set -euo pipefail

SERVICE="we-grow-dobro"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${PROJECT_DIR}/.venv"
cd "$PROJECT_DIR"

say() { printf '%s\n' "$*"; }

say "[1/4] Делаю резервную копию базы..."
if [ -f data/marathon.db ]; then
    cp data/marathon.db "data/marathon.db.backup-$(date +%Y%m%d-%H%M%S)"
    say "      Готово: data/marathon.db.backup-*"
else
    say "      Базы ещё нет — пропускаю."
fi

say "[2/4] Забираю свежий код..."
git pull --ff-only

say "[3/4] Обновляю зависимости..."
[ -x "${VENV}/bin/python" ] || { say "[ОШИБКА] Нет .venv — сначала запустите: sudo bash deploy.sh"; exit 1; }
"${VENV}/bin/python" -m pip install -r requirements.txt --quiet --disable-pip-version-check

say "[4/4] Перезапускаю службу..."
if systemctl list-unit-files | grep -q "^${SERVICE}.service"; then
    sudo systemctl restart "$SERVICE"
    sleep 3
    systemctl --no-pager --lines=0 status "$SERVICE" || true
    say ""
    say "Логи: journalctl -u ${SERVICE} -f"
else
    say "[ОШИБКА] Служба не установлена — запустите: sudo bash deploy.sh"
    exit 1
fi
