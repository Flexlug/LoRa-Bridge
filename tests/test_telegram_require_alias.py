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
    transport._bot.set_message_reaction = AsyncMock()
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
