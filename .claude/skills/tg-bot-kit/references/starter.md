# Каркас нового бота

Пошагово: от пустого репозитория до работающего бота. Тема любая — заявки, обучение,
опросы, учёт, запись на приём. Меняются сущности, не устройство.

## 1. Скелет

```
app/
  __init__.py  config.py  models.py  db.py  services.py
  texts.py  keyboards.py  emoji.py  export.py  scheduler.py  main.py
  bot/
    __init__.py  common.py  middlewares.py  channels.py  states.py
    handlers/__init__.py
  web/api.py
data/            контент проекта и наборы эмодзи
scripts/         шесть проверок из SKILL.md
.env.example  render.yaml  requirements.txt  README.md  CHANGELOG.md
```

`requirements.txt`: `aiogram`, `SQLAlchemy[asyncio]`, `asyncpg`, `aiosqlite`,
`APScheduler`, `fastapi`, `uvicorn`, `python-dotenv`, `tzdata`, `openpyxl`, `Pillow`,
`aiohttp`, `pyflakes`.

## 2. `config.py`

Одна dataclass со всеми настройками, читается из окружения при импорте. Обязательно:

```python
def _normalize_db_url(raw: str) -> str:
    """postgres:// -> postgresql+asyncpg:// и выброс параметров, которых asyncpg не знает."""
    url = (raw or "").strip()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix):]
    if "+asyncpg" in url:
        base, _, query = url.partition("?")
        kept = [p for p in query.split("&") if p.split("=", 1)[0] in _ASYNCPG_URL_PARAMS]
        if "-pooler." in base:                      # PgBouncer: prepared statements ломаются
            kept.append("prepared_statement_cache_size=0")
        url = base + ("?" + "&".join(kept) if kept else "")
    return url
```

Дальше: `bot_token`, `database_url`, `admin_ids`, `staff_ids`, id каналов, `tz_name`,
контакт поддержки, часы рассылок, `premium_emoji`, `voice_name`. Списки доступа —
со здравым значением по умолчанию, чтобы потеря переменной не открывала панель всем.

## 3. `db.py` — автомиграция

Инструмента миграций нет. На старте сравниваем модели с таблицами и добавляем
недостающие колонки:

```python
async def _add_missing_columns(conn) -> None:
    for table in Base.metadata.sorted_tables:
        existing = {c["name"] for c in await conn.run_sync(
            lambda sync: inspect(sync).get_columns(table.name))}
        for col in table.columns:
            if col.name in existing:
                continue
            await conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {_column_ddl(col)}"))
```

`_column_ddl` обязан понимать: JSON-колонки с `default=list` / `default=dict`
(иначе они молча не создаются) и `NOT NULL` со значением по умолчанию.
Удаление и переименование колонок — только руками.

## 4. `bot/common.py` — четыре функции, на которых держится интерфейс

- `session()` — контекст сессии базы;
- `load_user(s, tg_user)` — найти или завести пользователя, обновить логин и снять
  лишние права из базы;
- `edit(cq, text, markup)` — редактирование того же сообщения; ловит
  «message is not modified»; при подписи под фото — `editMessageCaption`;
- `edit_anchor(bot, chat_id, state, text, markup)` — обновить якорное сообщение, когда
  пользователь ответил текстом или файлом (его сообщение при этом удаляется).

## 5. Роли и фильтры

```python
class IsStaff(BaseFilter):          # именно BaseFilter, не класс с __call__
    async def __call__(self, event) -> bool:
        return settings.is_staff(event.from_user.id)

router.message.filter(IsStaff())
router.callback_query.filter(IsStaff())
```

Команды прячутся областями видимости: `BotCommandScopeAllPrivateChats` для всех,
`BotCommandScopeChat` — персонально сотрудникам. Скрытая команда владельца не
публикуется вообще.

## 6. Каналы модерации

Одна функция публикует карточку, вторая её переписывает после решения:

```python
async def post_card(bot, s, obj): ...    # медиа, затем текст с кнопками; id сохраняем
async def update_card(bot, s, obj): ...  # тот же id, кнопки убраны, видно кто и когда решил
```

Канал не привязан → карточка уходит сотрудникам в личку, бот работает и так.
Кнопки в канале обязаны быть под фильтром доступа: канал видят многие.

## 7. Уведомления

Правило без исключений: **любое решение сотрудника заканчивается сообщением человеку.**
Приняли, отклонили, перевели, начислили, обнулили, удалили — обо всём он узнаёт сам.
Проверяется скриптом `audit_pushes` (см. `references/checks.md`), а не памятью.

## 8. Первый запуск

```bash
cp .env.example .env          # токен и ADMIN_IDS
python -m app.main            # бот + планировщик + служебный порт
python -m scripts.preflight   # что осталось сделать руками
```

## 9. CLAUDE.md нового репозитория

Чтобы правила действовали с первого сообщения, положите в корень:

```markdown
# <Название проекта>

Telegram-бот на aiogram 3. Работаем по навыкам: `tg-bot-kit` (каркас, настройки, проверки),
`tg-bot-style` (оформление экранов и кнопок), `agent-workflow` (порядок работы и ответы).

- Вся бизнес-логика — в `app/services.py`, Telegram — в `app/bot/handlers/`.
- Настройки только через переменные окружения, значения в коде не хардкодим.
- После каждой правки: запись в `CHANGELOG.md` и прогон всех проверок из `scripts/`.
- Ответ заказчику — строго по пунктам: «Что добавил» и «Что сделать вам».
```
