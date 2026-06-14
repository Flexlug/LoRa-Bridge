"""Loop-guard: гасим собственное TX-эхо на LoRa-пути (A1).

Обычно узел свой TX на RX не отдаёт, но MeshCore room server может эхнуть наш
пост назад (R8) — поэтому держим «recently-TX» множество с TTL и сверяем входящие.
"""
from __future__ import annotations

import time
from collections import OrderedDict

from ..domain.models import Message


class LoopGuard:
    def __init__(self, ttl_seconds: float, *, _clock=time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._recent_tx: "OrderedDict[str, float]" = OrderedDict()
        self._clock = _clock

    def mark_sent(self, text: str) -> None:
        """Запомнить полезную нагрузку, которую мы только что отправили в эфир."""
        now = self._clock()
        self._evict(now)
        self._recent_tx[text] = now
        self._recent_tx.move_to_end(text)

    def is_echo(self, msg: Message) -> bool:
        """``True`` — это эхо нашего недавнего TX (не пробрасываем в фан-аут)."""
        now = self._clock()
        self._evict(now)
        return msg.text in self._recent_tx

    def _evict(self, now: float) -> None:
        deadline = now - self._ttl
        while self._recent_tx:
            _, ts = next(iter(self._recent_tx.items()))
            if ts >= deadline:
                break
            self._recent_tx.popitem(last=False)
