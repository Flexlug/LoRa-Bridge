"""Dedup mesh-дубликатов — ТОЛЬКО для LoRa-пути (A3).

Без надёжного id опираемся на хеш ``(sender_uid, text)`` + TTL-окно: timestamp у
LoRa может отсутствовать, поэтому окно времени — основной механизм истечения.
"""
from __future__ import annotations

import time
from collections import OrderedDict

from ..domain.models import Message


class TtlDedup:
    def __init__(self, ttl_seconds: float, *, _clock=time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._seen: "OrderedDict[str, float]" = OrderedDict()
        self._clock = _clock

    @staticmethod
    def _key(msg: Message) -> str:
        # origin_tag, если транспорт его проставил; иначе контентный хеш.
        if msg.origin_tag:
            return msg.origin_tag
        return f"{msg.sender.transport_uid}\x00{msg.text}"

    def accept(self, msg: Message) -> bool:
        """``True`` — сообщение новое (пропускаем); ``False`` — дубль (глотаем)."""
        now = self._clock()
        self._evict(now)
        key = self._key(msg)
        if key in self._seen:
            self._seen.move_to_end(key)
            return False
        self._seen[key] = now
        return True

    def _evict(self, now: float) -> None:
        deadline = now - self._ttl
        while self._seen:
            oldest_key, ts = next(iter(self._seen.items()))
            if ts >= deadline:
                break
            self._seen.popitem(last=False)
