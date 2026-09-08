#!/usr/bin/env bash
# Установка бота марафона на Ubuntu/Debian VPS как systemd-службы.
# Запуск из папки проекта:  sudo bash deploy.sh
# Скрипт идемпотентный — можно запускать повторно, настройки и база не пострадают.
set -euo pipefail

SERVICE="we-grow-dobro"
UNIT="/etc/systemd/system/${SERVICE}.service"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${PROJECT_DIR}/.venv"
ENV_FILE="${PROJECT_DIR}/.env"
LINE="===================================================="

say()  { printf '%s\n' "$*"; }
head2() { printf '\n%s\n  %s\n%s\n' "$LINE" "$*" "$LINE"; }
fail() { printf '\n[ОШИБКА] %s\n\n' "$*" >&2; exit 1; }

head2 "We Grow Dobro — установка бота на сервер"

# --- 1. Права ---
[ "$(id -u)" -eq 0 ] || fail "Запустите скрипт через sudo:  sudo bash deploy.sh"

# Служба будет работать от владельца папки проекта, а не от root.
RUN_USER="$(stat -c '%U' "$PROJECT_DIR")"
[ "$RUN_USER" = "UNKNOWN" ] && RUN_USER="root"
say "Каталог проекта : $PROJECT_DIR"
say "Служба от имени : $RUN_USER"

# --- 2. Системные пакеты ---
say ""
say "[1/6] Проверяю системные пакеты..."
NEEDED=()
command -v python3   >/dev/null 2>&1 || NEEDED+=(python3)
command -v curl      >/dev/null 2>&1 || NEEDED+=(curl)
command -v git       >/dev/null 2>&1 || NEEDED+=(git)
python3 -c 'import venv' >/dev/null 2>&1 || NEEDED+=(python3-venv)
python3 -c 'import ensurepip' >/dev/null 2>&1 || NEEDED+=(python3-pip)
if [ ${#NEEDED[@]} -gt 0 ]; then
    say "      Ставлю: ${NEEDED[*]}"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq "${NEEDED[@]}" >/dev/null
else
    say "      Всё на месте."
fi

# --- 3. Версия Python ---
PYV="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
say "[2/6] Python $PYV"
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    say ""
    say "  На сервере Python $PYV, а нужен 3.10 или новее."
    say "  Скорее всего образ системы слишком старый:"
    say "    Ubuntu 18 -> Python 3.6  (не подойдёт)"
    say "    Ubuntu 20 -> Python 3.8  (не подойдёт)"
    say "    Ubuntu 22 -> Python 3.10 (подойдёт)"
    say ""
    fail "Переустановите сервер с образом Ubuntu 22 и запустите deploy.sh снова."
fi

# --- 4. Связь с Telegram ---
say "[3/6] Проверяю доступ к api.telegram.org..."
if curl -sS -m 15 -o /dev/null https://api.telegram.org 2>/dev/null; then
    say "      Есть связь."
else
    say ""
    say "      [ВНИМАНИЕ] Сервер не достучался до api.telegram.org."
    say "      Скорее всего Telegram блокируется в стране/дата-центре этого сервера."
    say "      Бот установится, но работать не сможет, пока это не решено:"
    say "        - возьмите сервер в другой стране (Нидерланды, Германия, Финляндия, Казахстан), либо"
    say "        - настройте на сервере выход через прокси/VPN."
    say ""
fi

# --- 5. Виртуальное окружение и зависимости ---
say "[4/6] Готовлю виртуальное окружение и зависимости..."
[ -x "${VENV}/bin/python" ] || python3 -m venv "$VENV"
"${VENV}/bin/python" -m pip install --upgrade pip --quiet --disable-pip-version-check
"${VENV}/bin/python" -m pip install -r "${PROJECT_DIR}/requirements.txt" --quiet --disable-pip-version-check

# --- 6. Настройки (.env) ---
say "[5/6] Проверяю настройки..."
[ -f "$ENV_FILE" ] || cp "${PROJECT_DIR}/.env.example" "$ENV_FILE"

token_ok() {
    "${VENV}/bin/python" - "$ENV_FILE" <<'PY'
import sys
from dotenv import dotenv_values
t = (dotenv_values(sys.argv[1]).get("BOT_TOKEN") or "").strip()
sys.exit(0 if t and not t.startswith("123456:ABC") else 1)
PY
}

set_env_var() {  # set_env_var КЛЮЧ ЗНАЧЕНИЕ — заменяет строку в .env, не трогая остальное
    "${VENV}/bin/python" - "$ENV_FILE" "$1" "$2" <<'PY'
import sys
path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path, encoding="utf-8").read().splitlines()
out, done = [], False
for line in lines:
    if line.strip().startswith(f"{key}=") and not done:
        out.append(f"{key}={value}")
        done = True
    else:
        out.append(line)
if not done:
    out.append(f"{key}={value}")
open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
}

if ! token_ok; then
    say ""
    say "$LINE"
    say "  Нужен токен бота"
    say "$LINE"
    say ""
    say "  1) Напишите @BotFather команду /newbot и получите токен"
    say "     вида 8123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    say "  2) Напишите @userinfobot — он пришлёт ваш Telegram id (число)"
    say ""
    printf "  Вставьте BOT_TOKEN: "
    read -r INPUT_TOKEN
    [ -n "${INPUT_TOKEN:-}" ] || fail "Токен не введён. Запустите deploy.sh снова."
    set_env_var BOT_TOKEN "$(printf '%s' "$INPUT_TOKEN" | tr -d '[:space:]')"

    printf "  Ваш Telegram id (через запятую можно несколько): "
    read -r INPUT_ADMINS
    [ -n "${INPUT_ADMINS:-}" ] && set_env_var ADMIN_IDS "$(printf '%s' "$INPUT_ADMINS" | tr -d '[:space:]')"

    token_ok || fail "Токен выглядит некорректно. Проверьте .env и запустите deploy.sh снова."
    say ""
    say "  Настройки сохранены."
fi

chown "$RUN_USER" "$ENV_FILE" 2>/dev/null || true
chmod 600 "$ENV_FILE"          # в файле лежит токен бота
mkdir -p "${PROJECT_DIR}/data"
chown -R "$RUN_USER" "${PROJECT_DIR}/data" "$VENV" 2>/dev/null || true

# --- 7. systemd ---
say "[6/6] Настраиваю автозапуск (systemd)..."
cat > "$UNIT" <<UNITEOF
[Unit]
Description=We Grow Dobro — Telegram bot for the kindness marathon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
WorkingDirectory=${PROJECT_DIR}
ExecStart=${VENV}/bin/python -m app.main
Restart=always
RestartSec=10
# 78 = ошибка конфигурации (неверный токен): перезапуск не поможет, служба останавливается с понятным логом.
RestartPreventExitStatus=78
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNITEOF

systemctl daemon-reload
systemctl enable "$SERVICE" --quiet
systemctl restart "$SERVICE"
sleep 3

head2 "Готово"
systemctl --no-pager --lines=0 status "$SERVICE" || true
say ""
if systemctl is-active --quiet "$SERVICE"; then
    say "  Бот запущен. Откройте его в Telegram и напишите /start"
else
    say "  [ВНИМАНИЕ] Служба не запустилась. Посмотрите причину:"
    say "      journalctl -u ${SERVICE} -n 50 --no-pager"
fi
say ""
say "  Полезные команды:"
say "    journalctl -u ${SERVICE} -f          — логи в реальном времени"
say "    systemctl status ${SERVICE}          — статус"
say "    sudo systemctl restart ${SERVICE}    — перезапустить"
say "    sudo systemctl stop ${SERVICE}       — остановить"
say "    bash update.sh                       — обновить код и перезапустить"
say ""
say "  Настройки — файл .env в этой папке. После правки: sudo systemctl restart ${SERVICE}"
say ""
