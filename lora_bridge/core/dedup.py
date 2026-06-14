"""Dedup mesh-дубликатов — ТОЛЬКО для LoRa-пути (A3).

Без надёжного id опираемся на хеш ``(sender_uid, text)`` + TTL-окно: timestamp у
LoRa может отсутствовать, поэтому окно времени — основной механизм истечения.
"""
from __future__ import annotations

import time

from .ttl_window import TtlWindow
from ..domain.models import Message


class TtlDedup:
    def __init__(self, ttl_seconds: float, *, _clock=time.monotonic) -> None:
        self._window = TtlWindow(ttl_seconds, _clock=_clock)

    @staticmethod
    def key(msg: Message) -> str:
        # origin_tag, если транспорт его проставил; иначе контентный хеш.
        if msg.origin_tag:
            return msg.origin_tag
        return f"{msg.sender.transport_uid}\x00{msg.text}"

    def accept(self, msg: Message) -> bool:
        """``True`` — сообщение новое (пропускаем); ``False`` — дубль (глотаем)."""
        return self._window.add_if_absent(self.key(msg))
