"""Шов транспорт-локальных команд: гарантия «команда не течёт в pipeline»."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiogram.types import Chat, Message, Update, User

from lora_bridge.transports.telegram.commands import ALL_COMMAND_METAS, command_menu, render_help
from lora_bridge.transports.telegram.moderation.roles import Role
from lora_bridge.transports.telegram.moderation.store import ModerationStore
from lora_bridge.transports.telegram.transport import TelegramTransport

_FAKE_TOKEN = "123456:AAFakeFakeFakeFakeFakeFakeFakeFakeFak"
_OWNER_ID = 1


async def _make_store() -> ModerationStore:
    s = ModerationStore(":memory:")
    await s.start()
    return s


def _make_transport_no_commands() -> TelegramTransport:
    config = SimpleNamespace(token=_FAKE_TOKEN, commands=None)
    transport = TelegramTransport("tg", config)  # type: ignore[arg-type]
    transport._bot.session = AsyncMock()
    transport._hub.publish = AsyncMock()
    return transport


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


def _update(text: str, user_id: int = 2) -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=10,
            date=dt.datetime(2024, 1, 1),
            chat=Chat(id=1, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="tester"),
            text=text,
        ),
    )


async def _feed(transport: TelegramTransport, text: str, user_id: int = 2) -> None:
    await transport._dp.feed_update(transport._bot, _update(text, user_id))


async def test_known_command_does_not_publish() -> None:
    transport = await _make_transport_with_commands()
    await _feed(transport, "/ping")
    transport._hub.publish.assert_not_called()
    sent = transport._bot.session.await_args.args[1]
    assert sent.text == "pong"


async def test_unknown_command_does_not_publish() -> None:
    transport = await _make_transport_with_commands()
    await _feed(transport, "/no_such_command arg")
    transport._hub.publish.assert_not_called()
    transport._bot.session.assert_awaited()


async def test_plain_text_is_published_to_pipeline() -> None:
    transport = await _make_transport_with_commands()
    await _feed(transport, "привет, мост")
    transport._hub.publish.assert_awaited_once()
    published = transport._hub.publish.await_args.args[0]
    assert published.text == "привет, мост"
    transport._bot.session.assert_not_awaited()


async def test_command_without_commands_block_does_not_publish() -> None:
    transport = _make_transport_no_commands()
    await _feed(transport, "/ping")
    transport._hub.publish.assert_not_called()


async def test_help_lists_commands_for_user_role() -> None:
    help_text = render_help([m for m in ALL_COMMAND_METAS if m.min_role <= Role.USER])
    assert "/ping" in help_text
    assert "/help" in help_text


async def test_help_hides_moderator_commands_from_user() -> None:
    help_text = render_help([m for m in ALL_COMMAND_METAS if m.min_role <= Role.USER])
    assert "/ban" not in help_text


async def test_help_shows_moderator_commands_to_moderator() -> None:
    help_text = render_help([m for m in ALL_COMMAND_METAS if m.min_role <= Role.MODERATOR])
    assert "/ban" in help_text


def test_command_menu_filters_by_role() -> None:
    user_menu = command_menu(ALL_COMMAND_METAS, Role.USER)
    mod_menu = command_menu(ALL_COMMAND_METAS, Role.MODERATOR)
    user_names = {c.command for c in user_menu}
    mod_names = {c.command for c in mod_menu}
    assert "ping" in user_names
    assert "ban" not in user_names
    assert "ban" in mod_names


async def test_start_registers_default_command_menu() -> None:
    transport = await _make_transport_with_commands()
    transport._bot.get_me = AsyncMock(return_value=SimpleNamespace(username="bot", id=7))
    transport._bot.set_my_commands = AsyncMock()

    async def _no_poll(*args: object, **kwargs: object) -> None:
        return None

    transport._dp.start_polling = _no_poll  # type: ignore[method-assign]
    await transport.start()
    if transport._poll_task is not None:
        transport._poll_task.cancel()

    transport._bot.set_my_commands.assert_awaited()
