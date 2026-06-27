"""Форматирование исходящего текста в Telegram ``send()`` по типу отправителя.

Единое правило: bridge-уведомления и сообщения без известного имени (каналы, где
ник уже в тексте) идут как есть; всё, у чего есть ``display_name`` (TG-юзер ИЛИ
резолвнутый автор room-server поста), — жирным префиксом ``<b>Имя</b>: текст``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from lora_bridge.domain.models import (
    BRIDGE_TRANSPORT_UID,
    ChannelRef,
    Identity,
    LORA_SENDER_UID,
    Message,
    messenger_channel,
)
from lora_bridge.transports.telegram.transport import TelegramTransport

_FAKE_TOKEN = "123456:AAFakeFakeFakeFakeFakeFakeFakeFakeFak"


def _make_transport() -> TelegramTransport:
    config = SimpleNamespace(token=_FAKE_TOKEN)
    transport = TelegramTransport("tg", config)  # type: ignore[arg-type]
    transport._bot.send_message = AsyncMock()  # type: ignore[method-assign]
    return transport


async def _sent_text(sender: Identity, text: str) -> str:
    transport = _make_transport()
    target = ChannelRef("tg", messenger_channel("123", None))
    msg = Message(id="1", source=ChannelRef("src", "x"), sender=sender, text=text)
    await transport.send(target, msg)
    return transport._bot.send_message.call_args.args[1]  # type: ignore[attr-defined]


async def test_bridge_notice_passes_through() -> None:
    sender = Identity(display_name="bridge", transport_uid=BRIDGE_TRANSPORT_UID)
    assert await _sent_text(sender, "нода offline") == "нода offline"


async def test_channel_message_without_name_passes_through() -> None:
    # каналы: имя уже в тексте, display_name пуст -> без жирного префикса
    sender = Identity(display_name="", transport_uid=LORA_SENDER_UID)
    assert await _sent_text(sender, "Alice: привет") == "Alice: привет"


async def test_room_server_author_gets_bold_prefix() -> None:
    # резолвнутый автор room-server поста (LoRa-источник, но с именем)
    sender = Identity(display_name="Alice", transport_uid=LORA_SENDER_UID)
    assert await _sent_text(sender, "привет") == "<b>Alice</b>: привет"


async def test_telegram_user_gets_bold_prefix() -> None:
    sender = Identity(display_name="Bob", transport_uid="42")
    assert await _sent_text(sender, "hi") == "<b>Bob</b>: hi"
