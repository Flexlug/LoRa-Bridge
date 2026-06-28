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

from aiogram.types import BotCommand, Chat, Message, Update, User

from lora_bridge.transports.telegram.commands import COMMANDS, command_menu, render_help
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
    sent = transport._bot.session.await_args.args[1]  # исходящий SendMessage
    assert sent.text == "pong"  # отработал ping-хэндлер, а не сеть unknown


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


async def test_help_lists_every_registered_command() -> None:
    # render_help() — чистая функция: реестр единственный источник правды,
    # каждый зарегистрированный command попадает в выхлоп с описанием.
    help_text = render_help()
    for spec in COMMANDS:
        assert f"/{spec.name}" in help_text
        assert spec.description in help_text


async def test_help_command_does_not_publish() -> None:
    transport = _make_transport()

    await _feed(transport, "/help")

    transport._hub.publish.assert_not_called()  # /help — транспорт-локальная, не в мост
    sent = transport._bot.session.await_args.args[1]  # исходящий SendMessage
    assert "доступных команд" in sent.text  # отработал show_help, а не сеть unknown


def test_command_menu_mirrors_registry() -> None:
    # Меню Telegram (set_my_commands) строится из того же реестра — без дрейфа.
    assert command_menu() == [
        BotCommand(command=spec.name, description=spec.description) for spec in COMMANDS
    ]


async def test_start_registers_command_menu() -> None:
    transport = _make_transport()
    transport._bot.get_me = AsyncMock(return_value=SimpleNamespace(username="bot", id=7))
    transport._bot.set_my_commands = AsyncMock()  # type: ignore[method-assign]

    async def _no_poll(*args: object, **kwargs: object) -> None:
        return None

    transport._dp.start_polling = _no_poll  # type: ignore[method-assign]
    await transport.start()
    if transport._poll_task is not None:
        transport._poll_task.cancel()

    transport._bot.set_my_commands.assert_awaited_once()
    sent = transport._bot.set_my_commands.await_args.args[0]
    assert sent == command_menu()
