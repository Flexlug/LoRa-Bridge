"""Кодирование Telegram-канала в ``ChannelRef.channel`` и обратно.

Домен кодирует ``(chat, topic)`` в opaque-строку через ``messenger_channel``
(``"chat"`` / ``"chat#topic"``); здесь — обратный разбор для отправки. Telegram
``chat_id`` и ``thread_id`` — целые, поэтому ``int``-разбор живёт в адаптере, а не
в (generic) домене. Round-trip с ``messenger_channel`` закреплён guard-тестом
(``tests/test_telegram_channels.py``) — если кодировки разойдутся, RX не сматчится
с комнатой.
"""

from __future__ import annotations

from typing import Optional


def split_channel(channel: str) -> tuple[int, Optional[int]]:
    """``"chat"`` / ``"chat#topic"`` → ``(chat_id, thread_id|None)``. Инверсия ``messenger_channel``."""
    if "#" in channel:
        chat, topic = channel.split("#", 1)
        return int(chat), int(topic)
    return int(channel), None
