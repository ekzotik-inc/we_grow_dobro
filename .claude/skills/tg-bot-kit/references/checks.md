# Шесть проверок: готовые заготовки

Копируются в новый проект как есть, меняются только названия сущностей.
Все они работают на **живой базе** и через **настоящий диспетчер aiogram** — только так
ловятся ошибки, которые не видит обычный юнит-тест.

## Фальшивая сессия Telegram

Основа трёх аудитов: наружу не уходит ничего, а вызовы можно посмотреть.

```python
class FakeSession:
    def __init__(self, bot):
        self.bot = bot

    async def __call__(self, bot, method, timeout=None):
        name = type(method).__name__
        if name in ("SendMessage", "SendPhoto"):
            sent.append((int(getattr(method, "chat_id", 0) or 0),
                         getattr(method, "text", None) or getattr(method, "caption", "") or ""))
        if name in ("SendMessage", "SendPhoto", "EditMessageText", "EditMessageCaption"):
            return Message(message_id=1, date=datetime.datetime.now(),
                           chat=Chat(id=getattr(method, "chat_id", 1) or 1, type="private"),
                           from_user=TgUser(id=1, is_bot=True, first_name="bot"), text="ok")
        if name == "GetMe":
            return TgUser(id=1, is_bot=True, first_name="bot", username="my_bot")
        if name == "SendMediaGroup":
            return []
        return True

    async def close(self): ...

bot = Bot("123:test", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
bot.session = FakeSession(bot)
dp = Dispatcher(storage=MemoryStorage()); dp.include_router(setup_routers())
```

Обновления собираются вручную и скармливаются диспетчеру:

```python
def cq_update(uid, data): ...   # CallbackQuery в личке
def msg_update(uid, text): ...  # обычное сообщение
await dp.feed_update(bot, cq_update(ADMIN, "adm:ok:1"))
```

## 1. `smoke_test.py`

Бизнес-логика без интерфейса плюс несколько прогонов через диспетчер.
Обязательно **на обеих базах**:

```bash
python -m scripts.smoke_test
DATABASE_URL="postgresql+asyncpg://user@host/db" python -m scripts.smoke_test
```

Что закрывать с первого дня: регистрация и её восстановление после перезапуска,
права (участник не попадает в панель — проверять прогоном через диспетчер!),
пересдача после отказа начинается с нуля, автомиграция добавляет новую колонку,
уведомления доходят, экспорт открывается.

Если правите ошибку — тест повторяет ровно тот сценарий, в котором она проявилась,
и его обязательно надо прогнать **на старом коде**, убедившись, что он падает.

## 2. `audit_callbacks.py`

Достаёт все `callback_data` прямо из исходника клавиатур регулярным выражением,
подставляет настоящие идентификаторы и нажимает каждую кнопку от лица каждой роли.

Ищет две вещи: падение обработчика и кнопку, у которой обработчика нет вовсе.
Ни один другой способ этого не находит.

## 3. `audit_texts.py`

Рендерит каждый экран на живых данных и проверяет: теги закрыты, `&` экранирован,
длина сообщения ≤ 4096, длина подписи под фото ≤ 1024. Печатает самые длинные экраны —
по этому списку видно, что скоро упрётся в лимит.

## 4. `audit_pushes.py`

Прогоняет **каждое** действие сотрудника через диспетчер и проверяет, что пользователю
ушло сообщение:

```python
async def act(dp, bot, actor, *steps):
    for step in steps:
        upd = msg_update(actor, step[1:]) if step.startswith(">") else cq_update(actor, step)
        await dp.feed_update(bot, upd)

def check(title, target, must_contain=""):
    got = [t for chat, t in sent if chat == target]
    if not got: problems.append(f"{title}: пользователь не получил уведомление")
    elif must_contain and not any(must_contain.lower() in t.lower() for t in got):
        problems.append(f"{title}: в уведомлении нет «{must_contain}»")
```

Сценарий пишется по списку действий панели: принять, отклонить, перевести, начислить,
снять, обнулить, удалить. Новое действие сотрудника — новая строчка в этом аудите.

Важно: сценарий должен быть честным. Если предыдущий шаг уже обнулил данные, молчание
бота на следующем — правильное поведение, а не находка.

## 5. `preview.py`

Печатает все экраны текстом на живых данных. Нужен, чтобы **прочитать** бота целиком:
опечатки, канцелярит и оборванные фразы автотест не поймает.

## 6. `preflight.py`

Печатает состояние настроек и список того, что человек должен сделать руками:
токен, платный тариф, каналы, справочники, картинки профиля, включение контента.
Заканчивается строкой `ПРОБЛЕМ: N` — ноль означает «можно запускать».

## Дополнительно

- `python -m pyflakes app scripts` — перед каждым коммитом;
- локальный PostgreSQL для проверки миграций поднимается от отдельного пользователя
  (`postgres` не запускается от root);
- генераторы картинок (`make_images`, `make_botpic`, `make_shots`, `make_deck`)
  рисуют обложки, аватар, скриншоты и презентацию из **живых текстов проекта** —
  тогда материалы не расходятся с ботом после первой же правки.
