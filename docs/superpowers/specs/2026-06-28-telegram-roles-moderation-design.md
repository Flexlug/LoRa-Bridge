# Telegram: ролевая система и модерация

**Дата**: 2026-06-28  
**Ветка**: claude/telegram-transport  
**Слой**: `lora_bridge/transports/telegram/`

---

## 1. Контекст

Telegram-адаптер уже имеет каркас команд (`commands/framework.py` + `commands/handlers.py`).
Этот дизайн добавляет: ролевую систему, персистентные настройки пользователей и команды
модерации — как **опциональный** блок, включаемый через конфиг.

---

## 2. Ролевая система

```python
class Role(IntEnum):
    USER      = 0
    MODERATOR = 1
    ADMIN     = 2
    OWNER     = 3
```

**Правила выдачи/отзыва ролей:**

- actor может выдать роль только строго ниже своей
- actor может отозвать роль только строго ниже своей
- Admin не может revoke другого Admin или Owner
- Никто не может revoke собственную роль (защита от самоблокировки)
- Owner не хранится в БД — только в конфиге (не может быть случайно revoke-нут)

---

## 3. База данных

Таблицы живут в том же SQLite-файле что `LORA_BRIDGE_DB` (разные таблицы, один файл).

```sql
CREATE TABLE IF NOT EXISTS roles (
    tg_id  INTEGER PRIMARY KEY,
    role   TEXT NOT NULL  -- 'admin' | 'moderator'
);

CREATE TABLE IF NOT EXISTS user_settings (
    tg_id       INTEGER PRIMARY KEY,
    alias       TEXT,            -- NULL = не задан
    transliter  INTEGER DEFAULT 0,   -- 0/1
    disabled    INTEGER DEFAULT 0,   -- 0/1
    banned_name TEXT             -- display_name на момент бана; NULL если банили по ID
);
```

---

## 4. Конфиг

`commands` — опциональный вложенный блок в `TelegramMessengerConfig`.
Отсутствие блока = команды полностью выключены (роутер не регистрируется).

```yaml
messengers:
  - id: telegram-main
    kind: telegram
    token: ${TG_BOT_TOKEN}
    commands:                  # опционально; без него — бот команды не обрабатывает
      owner_id: 123456789      # Telegram user ID владельца; обязателен внутри блока
      alias_max_chars: 16      # макс длина alias (дефолт 16)
```

Pydantic-модели:

```python
class TelegramCommandsConfig(BaseModel):
    owner_id: int
    alias_max_chars: int = 16

class TelegramMessengerConfig(BaseMessengerConfig):
    kind: Literal["telegram"]
    token: str
    commands: Optional[TelegramCommandsConfig] = None
```

---

## 5. Модульная структура

```
transports/telegram/
  moderation/
    __init__.py
    roles.py      # Role enum, can_grant(), can_revoke(), get_role(store, owner_id, tg_id)
    store.py      # ModerationStore: async SQLite CRUD для roles + user_settings
  commands/
    framework.py  # CommandSpec(+min_role), build_command_router, render_help, command_menu
    handlers.py   # базовые команды: ping, help
    moderation.py # make_moderation_commands(store, cfg) → list[CommandSpec]
```

`make_moderation_commands` — фабрика: замыкание над `store` и `cfg`, возвращает
`list[CommandSpec]` с уже встроенными зависимостями. Каркас не знает о конкретных командах.

---

## 6. Расширение CommandSpec и каркаса

```python
@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    handler: CommandHandler
    min_role: Role = Role.USER
```

`build_command_router(transport_id, commands, store, owner_id)`:

- для каждого `CommandSpec` оборачивает handler в permission-check (роль вызывающего < `min_role` → "Недостаточно прав.", выход)
- регистрирует сеть неизвестных команд последней (инвариант: команды не текут в pipeline)

`render_help(commands, caller_role)` — фильтрует по `spec.min_role <= caller_role`.

`command_menu(commands, role)` — фильтрует аналогично, для `set_my_commands`.

---

## 7. Меню Telegram (per-user)

- При старте: `set_my_commands(user_commands, scope=BotCommandScopeDefault)` — USER-уровень для всех
- При старте: для каждого non-USER из БД — `set_my_commands(role_commands, scope=BotCommandScopeChatMember(chat_id, user_id))`
- При grant/revoke: один вызов `set_my_commands` / `delete_my_commands` для затронутого пользователя в текущем чате

---

## 8. Команды

| Команда | min_role | Аргументы | Описание |
|---------|----------|-----------|----------|
| `/ping` | USER | — | проверка живости |
| `/help` | USER | — | список команд по роли |
| `/set-alias [alias]` | USER | без арг. = сброс себе | alias себе; max `alias_max_chars` символов |
| `/set-alias @user\|id alias` | MODERATOR | — | alias другому |
| `/set-transliter` | USER | — | тогл транслитерации себе |
| `/set-transliter @user\|id` | MODERATOR | — | тогл транслитерации другому |
| `/ban` | MODERATOR | reply или @user\|id | запретить бриджинг TG→LoRa; реакция 🚫 |
| `/unban` | MODERATOR | reply или @user\|id | снять бан |
| `/banlist` | MODERATOR | — | список забаненных с mention-ами |
| `/role grant\|revoke admin\|moderator @user\|id` | ADMIN | — | управление ролями |

**Идентификация пользователя в командах** (для `/ban`, `/unban`, `/set-alias`, `/set-transliter`):
1. Reply на сообщение — берём `from_user` из реплаемого сообщения
2. Иначе — первый аргумент: `@username` или числовой ID

**Alias constraints**: только при `/set-alias`; длина ≤ `alias_max_chars` (дефолт 16);
если превышена — команда отвечает ошибкой с указанием лимита.

---

## 9. Интеграция с `on_message`

```python
async def on_message(self, message: TgMessage) -> None:
    user_id = message.from_user.id if message.from_user else None
    if user_id and await self._store.is_disabled(user_id):
        await self._reactions.report_disabled(message)  # реакция 🚫
        return
    settings = await self._store.get_user_settings(user_id) if user_id else None
    await self._hub.publish(self.normalize(message, settings))
```

`normalize(message, settings)`:
- если `settings.alias` задан — подставляет как `display_name`
- если `settings.transliter` — транслитерирует `message.text` кириллица→латиница

---

## 10. `/banlist` формат

```
Забаненные пользователи:
• <a href="tg://user?id=111">Vasya Pupkin</a> (alias: Вася)
• <a href="tg://user?id=222">222</a>
```

Текст ссылки: `banned_name` если не NULL, иначе строка tg_id.
Alias показывается если задан. Сообщение отправляется с `parse_mode="HTML"`.

---

## 11. Транслитерация

Реализуется как чистая функция `transliterate(text: str) -> str` в `moderation/`.
Отдельный маппинг кириллица→латиница без внешних зависимостей (стандартная таблица замен).

---

## 12. Что НЕ входит в этот дизайн

- Модерация LoRa→TG направления (LoRa-пользователи не имеют TG identity)
- Временные баны с TTL — отдельная фича

---

## 13. Audit log

### Таблица

```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,    -- unix timestamp (int, не float)
    actor_id    INTEGER NOT NULL,
    actor_name  TEXT,                -- alias если есть, иначе display_name на момент действия
    action      TEXT NOT NULL,       -- см. ниже
    target_id   INTEGER,             -- NULL для действий без цели
    target_name TEXT,                -- display_name / alias цели на момент действия
    detail      TEXT                 -- доп. контекст: "role: admin", "alias: Вася"
);
```

Логируемые действия (`action`):

| action | Команда | detail |
|--------|---------|--------|
| `ban` | `/ban` | — |
| `unban` | `/unban` | — |
| `grant` | `/role grant` | `"role: admin"` / `"role: moderator"` |
| `revoke` | `/role revoke` | `"role: admin"` / `"role: moderator"` |
| `set_alias` | `/set-alias` | `"alias: Вася"` или `"alias: сброшен"` |

`/set-transliter` не логируется.

### Команда `/audit`

| Параметр | Значение |
|----------|----------|
| min_role | MODERATOR |
| Аргументы | — |
| Размер страницы | 10 записей (фиксировано) |

Формат вывода одной страницы (HTML, parse_mode="HTML"):

```
Журнал действий (стр. 2/5):

2026-06-28 14:32  <a href="tg://user?id=111">Admin</a>  ban  →  <a href="tg://user?id=222">Vasya</a>
2026-06-28 13:10  <a href="tg://user?id=111">Admin</a>  grant  →  <a href="tg://user?id=333">Ivan</a>  [role: moderator]
```

Inline-кнопки под сообщением:

```
[ ← ]  [ 2 / 5 ]  [ → ]
```

- `[ ← ]` и `[ → ]` — callback `audit:prev:N` / `audit:next:N`; неактивная сторона показывается как пустая кнопка (или скрывается)
- `[ 2 / 5 ]` — кнопка-пустышка (callback игнорируется), только индикатор
- При нажатии: `edit_message_text` + `edit_message_reply_markup` — сообщение обновляется на месте

### Инфраструктура callback-хендлеров

`build_command_router` расширяется: принимает опциональный список `CallbackSpec`
(аналог `CommandSpec` для `callback_query`). Callback-хендлеры регистрируются
в том же роутере через `router.callback_query.register`.

```python
@dataclass(frozen=True)
class CallbackSpec:
    prefix: str          # фильтр: callback_data.startswith(prefix)
    handler: CallbackHandler
    min_role: Role = Role.USER
```

Permission-check для callback — аналогичен командам (роль по `callback.from_user.id`).

---

## 14. Автогенерация команд в mkdocs

### Проблема

`CommandSpec` содержит callable-хендлер — он не может быть импортирован при сборке
документации без runtime-зависимостей (SQLite, бот-токен).

### Решение: разделение метаданных и хендлеров

Вводится `CommandMeta` — чистый dataclass без callable:

```python
@dataclass(frozen=True)
class CommandMeta:
    name: str
    description: str
    min_role: Role = Role.USER
```

`CommandSpec` включает `CommandMeta` (или наследует):

```python
@dataclass(frozen=True)
class CommandSpec(CommandMeta):
    handler: CommandHandler = field(repr=False)
```

Каждый модуль команд экспортирует статический список метаданных:

- `commands/handlers.py` → `BASIC_COMMAND_METAS: list[CommandMeta]`
- `commands/moderation.py` → `MODERATION_COMMAND_METAS: list[CommandMeta]`

Агрегат в `commands/__init__.py`:

```python
ALL_COMMAND_METAS: list[CommandMeta] = BASIC_COMMAND_METAS + MODERATION_COMMAND_METAS
```

### Страница docs/gen_pages.py

В `main()` добавляется вызов `emit_commands_page`:

```python
emit_commands_page(
    path="reference/commands.md",
    title="Команды Telegram-бота",
)
```

Генерируемая страница — таблица:

| Команда | Мин. роль | Описание |
|---------|-----------|----------|
| `/ping` | user | проверка живости |
| `/ban` | moderator | запретить бриджинг TG→LoRa |
| … | … | … |

Страница добавляется в `nav` секцию `mkdocs.yml` вручную (один раз):

```yaml
nav:
  - Справочник команд: reference/commands.md
```

`gen_pages.py` импортирует только `ALL_COMMAND_METAS` из `commands/__init__.py`
(без store, без бота) — никаких runtime-зависимостей при сборке доки.

### Авто-индекс design-спеков

`gen_pages.py` получает ещё один вызов в `main()`:

```python
emit_specs_index(path="contributing/design-specs.md")
```

`emit_specs_index` сканирует `docs/superpowers/specs/*.md` через `pathlib.Path`,
сортирует по имени файла (имена начинаются с даты → хронологический порядок)
и генерирует виртуальную страницу-индекс:

```markdown
# Дизайн-спеки

| Дата | Название |
|------|----------|
| 2026-06-28 | [Telegram: ролевая система и модерация](../../superpowers/specs/2026-06-28-...) |
```

Название берётся из первого заголовка `# ...` файла (первая строка, начинающаяся с `# `).
Дата — первые 10 символов имени файла.

В `mkdocs.yml` одна статическая запись (добавляется вручную один раз):

```yaml
nav:
  - Для разработчиков:
      - Дизайн-спеки: contributing/design-specs.md
```

Сами спек-файлы mkdocs сервит автоматически (любой файл в `docs_dir` доступен по URL).
Индекс только агрегирует ссылки — новый спек появляется в индексе автоматически при
следующей сборке доки, без правки `mkdocs.yml`.
