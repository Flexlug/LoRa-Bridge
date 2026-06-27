"""Шов транспорт-локальных команд: гарантия «команда не течёт в pipeline».

Главный инвариант (принцип #10, закодирован тестом): любое сообщение-команда,
прогнанное через РЕАЛЬНЫЙ Dispatcher транспорта, обрабатывается локально и
**не** доходит до ``_hub.publish`` — то есть не попадает в общий мост LoRa.
Обычный текст, наоборот, обязан публиковаться.

Бот создаётся с синтаксически валидным фейк-токеном; на сеть не ходим —
``send_message`` подменяется, ответ команды перехватывается.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import Chat, Message, Update, User

from lora_bridge.transports.telegram.transport import TelegramTransport

_FAKE_TOKEN = "123456:AAFakeFakeFakeFakeFakeFakeFakeFakeFak"


def _make_transport() -> TelegramTransport:
    config = SimpleNamespace(token=_FAKE_TOKEN)
    transport = TelegramTransport("tg", config)  # type: ignore[arg-type]
    # message.answer() уходит в сеть через bot.session — глушим, чтобы ответ
    # команды (reply) не делал реальный HTTP-вызов с фейк-токеном.
    transport._bot.session = AsyncMock()  # type: ignore[assignment]
    transport._hub.publish = AsyncMock()  # type: ignore[method-assign]
    return transport


def _update(text: str) -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=10,
            date=dt.datetime(2024, 1, 1),
            chat=Chat(id=1, type="private"),
            from_user=User(id=2, is_bot=False, first_name="tester"),
            text=text,
        ),
    )


async def _feed(transport: TelegramTransport, text: str) -> None:
    await transport._dp.feed_update(transport._bot, _update(text))


async def test_known_command_does_not_publish() -> None:
    transport = _make_transport()

    await _feed(transport, "/ping")

    transport._hub.publish.assert_not_called()  # не протекло в pipeline
    transport._bot.session.assert_awaited()  # обработано локально (ответ боту)


async def test_unknown_command_does_not_publish() -> None:
    transport = _make_transport()

    await _feed(transport, "/no_such_command arg")

    transport._hub.publish.assert_not_called()  # namespace закрыт — не протекает
    transport._bot.session.assert_awaited()  # «неизвестная команда» в ответ


async def test_plain_text_is_published_to_pipeline() -> None:
    transport = _make_transport()

    await _feed(transport, "привет, мост")

    transport._hub.publish.assert_awaited_once()  # обычный текст идёт в мост
    published = transport._hub.publish.await_args.args[0]
    assert published.text == "привет, мост"
    transport._bot.session.assert_not_awaited()  # bridge-путь не отвечает в чат
