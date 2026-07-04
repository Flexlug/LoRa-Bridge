# Telegram: обязательный alias для бриджинга

**Дата**: 2026-07-04
**Ветка**: worktree-config-example-review
**Слой**: `lora_bridge/transports/telegram/`

---

## 1. Контекст

Alias уже существует (`docs/superpowers/specs/2026-06-28-telegram-roles-moderation-design.md`,
§2–4) как опциональный псевдоним, подставляемый в `display_name` при наличии. Пользователь
без alias сейчас всё равно бриджится — под своим Telegram full name.

Этот дизайн делает alias **обязательным условием** для бриджинга: сообщение пользователя без
alias в LoRa не уходит вообще (не публикуется в `Hub`), независимо от роли (включая OWNER).

---

## 2. Конфиг

Новое поле в `TelegramCommandsConfig` (`config/schema/messengers.py`):

```python
class TelegramCommandsConfig(BaseModel):
    owner_id: int
    alias_max_chars: int = 16
    require_alias: bool = True   # новое; без alias — сообщение не публикуется в Hub
```

Работает только когда блок `commands` включён — без него `/set_alias` недоступен и
`ModerationStore` не создаётся (`transport.py:92-100`), так что enforcement естественным
образом неактивен (соответствующая ветка `on_message` не вызывается).

---

## 3. Поведение в `on_message`

Проверка встаёт сразу после `is_disabled`, до `normalize`/`publish`:

```python
async def on_message(self, message: TgMessage) -> None:
    user_id = message.from_user.id if message.from_user else None
    settings: Optional[UserSettings] = None
    if user_id is not None and self._store is not None:
        if await self._store.is_disabled(user_id):
            await self._reactions.report_disabled(message)
            return
        settings = await self._store.get_user_settings(user_id)
        if self._require_alias and not settings.alias:
            await self._reactions.report_alias_required(message)
            asyncio.create_task(
                self._reactions.send_expiring_reply(message, ALIAS_REQUIRED_TEXT)
            )
            return
    await self._hub.publish(self.normalize(message, settings))
```

`self._require_alias` — читается один раз в `__init__` из
`config.commands.require_alias if config.commands else False`.

Правило единое для всех ролей — не проверяется `Role`, ветка одна.

Текст `ALIAS_REQUIRED_TEXT` учитывает, что командный роутер работает
`private_only=True` (`transport.py:120-124`) — `/set_alias` нельзя вызвать прямо в
группе, где идёт мост:

```
Установи alias в личке с ботом: /set_alias <имя> — иначе сообщения из этого чата
не долетают до LoRa.
```

---

## 4. UX-фидбэк (`reactions.py`)

Два новых метода на `ReactionFeedback`, по образцу существующего `report_disabled`
(реакция) и `_delete_after` из `commands/framework.py` (самоудаляющийся reply):

```python
_ALIAS_REPLY_TTL_S = 5.0   # тот же интервал, что _GROUP_DELETE_DELAY в commands/framework.py

async def report_alias_required(self, message: "TgMessage") -> None:
    """Реакция 🪪 на сообщение без alias (best-effort)."""
    try:
        await self._bot.set_message_reaction(
            message.chat.id, message.message_id,
            reaction=[ReactionTypeEmoji(emoji="🪪")],
        )
    except Exception:  # noqa: BLE001
        pass

async def send_expiring_reply(
    self, message: "TgMessage", text: str, delay: float = _ALIAS_REPLY_TTL_S
) -> None:
    """Reply, который сам удаляется через ``delay`` секунд. Исходное сообщение не трогаем."""
    try:
        bot_msg = await message.reply(text)
    except Exception:  # noqa: BLE001
        return
    await asyncio.sleep(delay)
    with suppress(Exception):
        await bot_msg.delete()
```

`send_expiring_reply` вызывается через `asyncio.create_task` в `on_message` — не блокирует
обработку следующих сообщений на время сна.

Проверка выполняется на **каждое** проигнорированное сообщение (без счётчика/флага
«уже предупредили») — реплика самоудаляется, так что повторный reply не копится в чате.

---

## 5. Что НЕ входит в этот дизайн

- Изменения схемы БД — не требуются (`user_settings` не меняется).
- Исключения по ролям — нет ни одного (OWNER/ADMIN/MODERATOR наравне с USER).
- Поведение для non-text сообщений — не меняется (уже не бриджатся, `F.text`-фильтр).
