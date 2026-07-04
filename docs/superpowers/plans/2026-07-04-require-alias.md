# Обязательный alias для бриджинга — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сообщения Telegram-пользователей без alias перестают публиковаться в `Hub` (не долетают до LoRa), пока пользователь не поставит себе alias через `/set_alias`.

**Architecture:** Новая проверка встраивается в `TelegramTransport.on_message`, сразу после существующей проверки `is_disabled` — тот же паттерн (best-effort реакция + `return` до `Hub.publish`). Управляется полем `require_alias: bool = True` в `TelegramCommandsConfig`; активно только когда включён блок `commands` (иначе `/set_alias` физически недоступен). UX-фидбэк — реакция 🪪 + самоудаляющийся reply, оба метода живут в `reactions.py` рядом с существующим `report_disabled`.

**Tech Stack:** Python, pydantic v2, aiogram, pytest (anyio mode=auto), aiosqlite.

Спека: `docs/superpowers/specs/2026-07-04-require-alias-design.md`.

---

### Task 1: Конфиг — поле `require_alias`

**Files:**
- Modify: `lora_bridge/config/schema/messengers.py:37-44`
- Test: `tests/test_config_schema.py`

- [ ] **Step 1: Написать падающие тесты**

Добавить в конец `tests/test_config_schema.py`:

```python
def test_telegram_commands_require_alias_default_true() -> None:
    cfg = TelegramCommandsConfig(owner_id=1)
    assert cfg.require_alias is True


def test_telegram_commands_require_alias_can_disable() -> None:
    cfg = TelegramCommandsConfig(owner_id=1, require_alias=False)
    assert cfg.require_alias is False
```

- [ ] **Step 2: Убедиться что тесты падают**

Run: `pytest tests/test_config_schema.py -k require_alias -v`
Expected: обе FAIL с `AttributeError: 'TelegramCommandsConfig' object has no attribute 'require_alias'`
(поле пока не объявлено в модели — pydantic по умолчанию молча игнорирует незнакомый kwarg,
`extra` не задан как `forbid` в этом файле).

- [ ] **Step 3: Добавить поле в схему**

В `lora_bridge/config/schema/messengers.py` заменить:

```python
class TelegramCommandsConfig(BaseModel):
    """Опциональный блок команд Telegram-бота. Отсутствие = команды выключены."""

    owner_id: int = Field(description="Telegram user ID владельца бота (роль OWNER).")
    alias_max_chars: int = Field(
        default=16,
        description="Максимальная длина псевдонима пользователя.",
    )
```

на:

```python
class TelegramCommandsConfig(BaseModel):
    """Опциональный блок команд Telegram-бота. Отсутствие = команды выключены."""

    owner_id: int = Field(description="Telegram user ID владельца бота (роль OWNER).")
    alias_max_chars: int = Field(
        default=16,
        description="Максимальная длина псевдонима пользователя.",
    )
    require_alias: bool = Field(
        default=True,
        description=(
            "Обязательность alias для бриджинга. Пока пользователь не поставит себе "
            "alias через /set_alias, его сообщения не публикуются в LoRa (реакция 🪪 "
            "+ самоудаляющееся напоминание). Действует для всех ролей без исключений."
        ),
    )
```

- [ ] **Step 4: Убедиться что тесты проходят**

Run: `pytest tests/test_config_schema.py -k require_alias -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Коммит**

```bash
git add lora_bridge/config/schema/messengers.py tests/test_config_schema.py
git commit -m "feat(config): поле require_alias в TelegramCommandsConfig (дефолт true)"
```

---

### Task 2: UX-фидбэк в `reactions.py`

**Files:**
- Modify: `lora_bridge/transports/telegram/reactions.py`
- Test: `tests/test_telegram_reactions.py`

- [ ] **Step 1: Написать падающие тесты**

В `tests/test_telegram_reactions.py` добавить импорт `SimpleNamespace` в блок импортов
(после `from unittest.mock import AsyncMock`):

```python
from types import SimpleNamespace
```

И добавить в конец файла:

```python
# --- ReactionFeedback: alias required ---------------------------------------


async def test_report_alias_required_sets_identity_card_emoji() -> None:
    bot = _bot()
    fb = ReactionFeedback(bot)
    message = SimpleNamespace(chat=SimpleNamespace(id=111), message_id=5)

    await fb.report_alias_required(message)

    bot.set_message_reaction.assert_awaited_once()
    _, kwargs = bot.set_message_reaction.call_args
    assert kwargs["reaction"][0].emoji == "🪪"


async def test_send_expiring_reply_sends_then_deletes_after_delay() -> None:
    bot = _bot()
    fb = ReactionFeedback(bot)
    sent = AsyncMock()
    message = AsyncMock()
    message.reply = AsyncMock(return_value=sent)

    await fb.send_expiring_reply(message, "текст", delay=0.03)

    message.reply.assert_awaited_once_with("текст")
    sent.delete.assert_not_awaited()  # ещё не истёк delay

    await asyncio.sleep(0.05)
    sent.delete.assert_awaited_once()


async def test_send_expiring_reply_swallows_reply_failure() -> None:
    bot = _bot()
    fb = ReactionFeedback(bot)
    message = AsyncMock()
    message.reply = AsyncMock(side_effect=RuntimeError("boom"))

    await fb.send_expiring_reply(message, "текст", delay=0.01)  # не должно бросать
```

- [ ] **Step 2: Убедиться что тесты падают**

Run: `pytest tests/test_telegram_reactions.py -k "alias_required or expiring" -v`
Expected: все три FAIL с `AttributeError: 'ReactionFeedback' object has no attribute 'report_alias_required'`
(и аналогично для `send_expiring_reply`).

- [ ] **Step 3: Реализовать методы**

В `lora_bridge/transports/telegram/reactions.py` добавить `suppress` в импорты
(строка 12, было `from __future__ import annotations`, дальше идёт пустая строка и `import asyncio`):

```python
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Optional
```

Добавить константу после `REACTION_DEBOUNCE_S = 2.0` (строка 42):

```python
# Время жизни self-destruct реплики с напоминанием про alias (секунды).
# Совпадает с _GROUP_DELETE_DELAY в commands/framework.py — единый UX-интервал
# для служебных сообщений бота, которые не должны копиться в чате.
ALIAS_REPLY_TTL_S = 5.0
```

Добавить два метода в класс `ReactionFeedback`, сразу после `report_disabled`
(после строки `pass`, перед `@staticmethod def _reaction_for`):

```python
    async def report_alias_required(self, message: "TgMessage") -> None:
        """Реакция 🪪 на сообщение без alias, когда он обязателен (best-effort)."""
        try:
            await self._bot.set_message_reaction(
                message.chat.id,
                message.message_id,
                reaction=[ReactionTypeEmoji(emoji="🪪")],
            )
        except Exception:  # noqa: BLE001
            pass

    async def send_expiring_reply(
        self, message: "TgMessage", text: str, delay: float = ALIAS_REPLY_TTL_S
    ) -> None:
        """Reply, который сам удаляется через ``delay`` секунд.

        Сам reply отправляется синхронно (быстрый API-вызов); удаление — фоновой
        задачей, чтобы не блокировать обработку следующих сообщений. Исходное
        сообщение пользователя не трогаем — удаляется только ответ бота.
        """
        try:
            bot_msg = await message.reply(text)
        except Exception:  # noqa: BLE001
            return
        asyncio.create_task(self._delete_reply_after(delay, bot_msg))

    @staticmethod
    async def _delete_reply_after(delay: float, reply: "TgMessage") -> None:
        await asyncio.sleep(delay)
        with suppress(Exception):
            await reply.delete()
```

- [ ] **Step 4: Убедиться что тесты проходят**

Run: `pytest tests/test_telegram_reactions.py -v`
Expected: все тесты в файле PASS (старые характеризационные + 3 новых).

- [ ] **Step 5: Коммит**

```bash
git add lora_bridge/transports/telegram/reactions.py tests/test_telegram_reactions.py
git commit -m "feat(telegram): report_alias_required + send_expiring_reply в ReactionFeedback"
```

---

### Task 3: Гейт в `on_message`

**Files:**
- Modify: `lora_bridge/transports/telegram/transport.py`
- Modify: `tests/test_telegram_commands.py:34-43` (не регрессировать существующие тесты)
- Test: `tests/test_telegram_require_alias.py` (новый файл)

- [ ] **Step 1: Защитить существующие тесты от регрессии**

Существующие тесты в `tests/test_telegram_commands.py` шлют текст от `user_id=2` без
alias — после Task 3 они попадут под новый гейт, если не отключить его явно. В
`_make_transport_with_commands` (строки 34-43) заменить:

```python
async def _make_transport_with_commands() -> TelegramTransport:
    store = await _make_store()
    config = SimpleNamespace(
        token=_FAKE_TOKEN,
        commands=SimpleNamespace(owner_id=_OWNER_ID, alias_max_chars=16),
    )
    transport = TelegramTransport("tg", config, _store=store)  # type: ignore[arg-type]
    transport._bot.session = AsyncMock()
    transport._hub.publish = AsyncMock()
    return transport
```

на:

```python
async def _make_transport_with_commands() -> TelegramTransport:
    store = await _make_store()
    config = SimpleNamespace(
        token=_FAKE_TOKEN,
        commands=SimpleNamespace(
            owner_id=_OWNER_ID, alias_max_chars=16, require_alias=False,
        ),
    )
    transport = TelegramTransport("tg", config, _store=store)  # type: ignore[arg-type]
    transport._bot.session = AsyncMock()
    transport._hub.publish = AsyncMock()
    return transport
```

Эти тесты проверяют роутинг команд, а не alias-гейт — `require_alias=False` изолирует
концерны, как и было до этой фичи.

- [ ] **Step 2: Запустить существующий файл, убедиться что всё ещё зелёный**

Run: `pytest tests/test_telegram_commands.py -v`
Expected: PASS (все тесты, как и до правки — фикстура просто явно фиксирует прежнее поведение).

- [ ] **Step 3: Написать падающие тесты для нового поведения**

Создать `tests/test_telegram_require_alias.py`:

```python
"""require_alias: сообщения без alias не публикуются в Hub (design от 2026-07-04)."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import Chat, Message, Update, User

from lora_bridge.transports.telegram.moderation.store import ModerationStore
from lora_bridge.transports.telegram.transport import ALIAS_REQUIRED_TEXT, TelegramTransport

_FAKE_TOKEN = "123456:AAFakeFakeFakeFakeFakeFakeFakeFakeFak"
_OWNER_ID = 1
_GROUP_CHAT_ID = -100123
_USER_ID = 2


async def _make_transport(*, require_alias: bool = True) -> TelegramTransport:
    store = ModerationStore(":memory:")
    await store.start()
    config = SimpleNamespace(
        token=_FAKE_TOKEN,
        commands=SimpleNamespace(
            owner_id=_OWNER_ID, alias_max_chars=16, require_alias=require_alias,
        ),
    )
    transport = TelegramTransport("tg", config, _store=store)  # type: ignore[arg-type]
    transport._bot.session = AsyncMock()
    transport._hub.publish = AsyncMock()
    return transport


def _group_update(text: str, user_id: int = _USER_ID) -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=10,
            date=dt.datetime(2024, 1, 1),
            chat=Chat(id=_GROUP_CHAT_ID, type="supergroup"),
            from_user=User(id=user_id, is_bot=False, first_name="tester"),
            text=text,
        ),
    )


async def _feed(transport: TelegramTransport, text: str, user_id: int = _USER_ID) -> None:
    await transport._dp.feed_update(transport._bot, _group_update(text, user_id))


async def test_message_without_alias_is_not_published() -> None:
    transport = await _make_transport(require_alias=True)
    await _feed(transport, "привет из моста")
    transport._hub.publish.assert_not_called()


async def test_message_without_alias_gets_identity_card_reaction() -> None:
    transport = await _make_transport(require_alias=True)
    await _feed(transport, "привет из моста")
    transport._bot.set_message_reaction.assert_awaited_once()
    _, kwargs = transport._bot.set_message_reaction.call_args
    assert kwargs["reaction"][0].emoji == "🪪"


async def test_message_without_alias_gets_expiring_reminder() -> None:
    transport = await _make_transport(require_alias=True)
    await _feed(transport, "привет из моста")
    transport._bot.session.assert_awaited()
    sent = transport._bot.session.await_args.args[1]
    assert ALIAS_REQUIRED_TEXT in sent.text


async def test_message_with_alias_is_published() -> None:
    transport = await _make_transport(require_alias=True)
    await transport._store.set_alias(_USER_ID, "Вася")
    await _feed(transport, "привет из моста")
    transport._hub.publish.assert_awaited_once()


async def test_require_alias_disabled_publishes_without_alias() -> None:
    transport = await _make_transport(require_alias=False)
    await _feed(transport, "привет из моста")
    transport._hub.publish.assert_awaited_once()


async def test_disabled_user_takes_priority_over_missing_alias() -> None:
    transport = await _make_transport(require_alias=True)
    await transport._store.ban_user(_USER_ID, "Vasya")
    await _feed(transport, "привет из моста")
    transport._hub.publish.assert_not_called()
    _, kwargs = transport._bot.set_message_reaction.call_args
    assert kwargs["reaction"][0].emoji == "🚫"  # бан приоритетнее alias-гейта, не 🪪
```

- [ ] **Step 4: Убедиться что новые тесты падают**

Run: `pytest tests/test_telegram_require_alias.py -v`
Expected: `test_message_without_alias_is_not_published` и `test_message_without_alias_gets_identity_card_reaction`
и `test_message_without_alias_gets_expiring_reminder` — FAIL (`hub.publish` вызывается, реакции/reply нет).
`test_message_with_alias_is_published`, `test_require_alias_disabled_publishes_without_alias`,
`test_disabled_user_takes_priority_over_missing_alias` — уже PASS (текущее поведение их не меняет),
это нормально для этого шага — они закрепляют то, что НЕ должно сломаться.

- [ ] **Step 5: Реализовать гейт**

В `lora_bridge/transports/telegram/transport.py` добавить константу после `log = logging.getLogger(__name__)`
(строка 50):

```python
log = logging.getLogger(__name__)

ALIAS_REQUIRED_TEXT = (
    "Установи alias в личке с ботом: /set_alias <имя> — иначе сообщения из "
    "этого чата не долетают до LoRa."
)
```

В `__init__` (строки 86-94) заменить:

```python
        self._store: Optional[ModerationStore] = None
        self._owner_id: int = 0
        # (tg_id, chat_id) — уже обновлённые scope; избегаем лишних API-вызовов
        self._cmd_scope_done: set[tuple[int, int]] = set()

        if config.commands is not None:
            owner_id = config.commands.owner_id
            self._owner_id = owner_id
```

на:

```python
        self._store: Optional[ModerationStore] = None
        self._owner_id: int = 0
        self._require_alias: bool = False
        # (tg_id, chat_id) — уже обновлённые scope; избегаем лишних API-вызовов
        self._cmd_scope_done: set[tuple[int, int]] = set()

        if config.commands is not None:
            owner_id = config.commands.owner_id
            self._owner_id = owner_id
            self._require_alias = getattr(config.commands, "require_alias", True)
```

(`getattr` с дефолтом — по образцу `moderation.py:108`, устойчиво к `SimpleNamespace`
в тестах без явного `require_alias`.)

В `on_message` (строки 188-197) заменить:

```python
    async def on_message(self, message: TgMessage) -> None:
        user_id = message.from_user.id if message.from_user else None
        if user_id is not None and self._store is not None:
            if await self._store.is_disabled(user_id):
                await self._reactions.report_disabled(message)
                return
            settings: Optional[UserSettings] = await self._store.get_user_settings(user_id)
        else:
            settings = None
        await self._hub.publish(self.normalize(message, settings))
```

на:

```python
    async def on_message(self, message: TgMessage) -> None:
        user_id = message.from_user.id if message.from_user else None
        if user_id is not None and self._store is not None:
            if await self._store.is_disabled(user_id):
                await self._reactions.report_disabled(message)
                return
            settings: Optional[UserSettings] = await self._store.get_user_settings(user_id)
            if self._require_alias and not settings.alias:
                await self._reactions.report_alias_required(message)
                await self._reactions.send_expiring_reply(message, ALIAS_REQUIRED_TEXT)
                return
        else:
            settings = None
        await self._hub.publish(self.normalize(message, settings))
```

- [ ] **Step 6: Убедиться что все тесты проходят**

Run: `pytest tests/test_telegram_require_alias.py tests/test_telegram_commands.py -v`
Expected: PASS (всё, включая ранее уже проходившие тесты из Step 4).

- [ ] **Step 7: Коммит**

```bash
git add lora_bridge/transports/telegram/transport.py \
        tests/test_telegram_commands.py tests/test_telegram_require_alias.py
git commit -m "feat(telegram): гейтовать бриджинг по наличию alias (require_alias)"
```

---

### Task 4: Документировать в `config.example.yaml`

**Files:**
- Modify: `config.example.yaml:44-47`

- [ ] **Step 1: Добавить комментарий про новое поле**

Заменить блок:

```yaml
    commands:                    # опционально; без блока — командный роутер не включается
      owner_id: 123456789        # Telegram user ID владельца (роль OWNER)
      alias_max_chars: 8         # максимальная длина псевдонима (по умолчанию 16)
```

на:

```yaml
    commands:                    # опционально; без блока — командный роутер не включается
      owner_id: 123456789        # Telegram user ID владельца (роль OWNER)
      alias_max_chars: 8         # максимальная длина псевдонима (по умолчанию 16)
      # require_alias: false     # без alias сообщения не бриджатся (по умолчанию true,
                                  # действует для всех ролей без исключений — см. /set_alias)
```

Ничего не тестируется — файл документационный (не парсится тестами), поэтому шага
verify/run для этого таска нет.

- [ ] **Step 2: Коммит**

```bash
git add config.example.yaml
git commit -m "docs(config): задокументировать require_alias в примере конфига"
```

---

### Task 5: Финальная проверка

- [ ] **Step 1: Полный прогон тестов**

Run: `pytest -q`
Expected: все тесты зелёные, 0 failed.

- [ ] **Step 2: Линт и типы**

Run: `ruff check`
Expected: `All checks passed!`

Run: `mypy lora_bridge`
Expected: `Success: no issues found`

Если что-то падает — почини на месте и закоммить отдельным коммитом с
понятным сообщением (не смешивать с коммитами предыдущих задач).
