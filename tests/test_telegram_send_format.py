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
    config = SimpleNamespace(token=_FAKE_TOKEN, commands=None)
    transport = TelegramTransport("tg", config)  # type: ignore[arg-type]
    transport._bot.send_message = AsyncMock()  # type: ignore[method-assign]
    return transport


async def _send_call(sender: Identity, text: str):  # type: ignore[no-untyped-def]
    transport = _make_transport()
    target = ChannelRef("tg", messenger_channel("123", None))
    msg = Message(id="1", source=ChannelRef("src", "x"), sender=sender, text=text)
    await transport.send(target, msg)
    return transport._bot.send_message.call_args  # type: ignore[attr-defined]


async def _sent_text(sender: Identity, text: str) -> str:
    call = await _send_call(sender, text)
    return call.args[1]


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


# --- HTML-экранирование (иначе спецсимволы из эфира роняют send_message) ---


async def test_bold_branch_escapes_special_chars_in_name_and_text() -> None:
    sender = Identity(display_name="A<b>x", transport_uid="42")
    assert await _sent_text(sender, "1 < 2 & 3") == "<b>A&lt;b&gt;x</b>: 1 &lt; 2 &amp; 3"


async def test_bold_branch_sets_html_parse_mode() -> None:
    sender = Identity(display_name="Bob", transport_uid="42")
    call = await _send_call(sender, "hi")
    assert call.kwargs["parse_mode"] == "HTML"


async def test_passthrough_does_not_interpret_html() -> None:
    # каналы/bridge: markup не добавляем -> спецсимволы тела НЕ экранируем и НЕ
    # парсим как HTML (иначе "1 < 2" уронил бы отправку)
    sender = Identity(display_name="", transport_uid=LORA_SENDER_UID)
    call = await _send_call(sender, "1 < 2 & 3")
    assert call.args[1] == "1 < 2 & 3"  # тело не тронуто
    assert call.kwargs.get("parse_mode") is None  # не интерпретируется как HTML
