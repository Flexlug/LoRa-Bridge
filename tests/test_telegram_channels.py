"""Характеризационные тесты кодирования Telegram-канала (channels.py).

Закрепляют поведение ``split_channel``, вынесенного из transport.py при
разбиении монолита, и — главное — round-trip-инвариант с доменным
``messenger_channel``: обе функции обязаны кодировать (chat, topic) согласованно,
иначе RX-сообщение не сматчится с комнатой (см. messenger_channel docstring).
"""

from __future__ import annotations

import pytest

from lora_bridge.domain.models import messenger_channel
from lora_bridge.transports.telegram.channels import split_channel


def test_split_channel_chat_only() -> None:
    assert split_channel("12345") == (12345, None)


def test_split_channel_with_topic() -> None:
    assert split_channel("12345#67") == (12345, 67)


def test_split_channel_negative_chat_id() -> None:
    # supergroup chat_id отрицателен — должен распарситься как int со знаком
    assert split_channel("-1001234567890") == (-1001234567890, None)


@pytest.mark.parametrize(
    ("chat", "topic"),
    [
        (12345, None),
        (12345, 67),
        (-1001234567890, None),
        (-1001234567890, 89),
    ],
)
def test_round_trip_with_messenger_channel(chat: int, topic: int | None) -> None:
    """split_channel — точная инверсия messenger_channel (принцип #10: инвариант тестом).

    Если кодировки разойдутся, ChannelRef из RX не совпадёт с записью комнаты.
    """
    encoded = messenger_channel(str(chat), str(topic) if topic is not None else None)
    assert split_channel(encoded) == (chat, topic)
