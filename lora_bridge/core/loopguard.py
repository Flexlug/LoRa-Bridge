"""Loop-guard: гасим собственное TX-эхо на LoRa-пути (A1).

Обычно узел свой TX на RX не отдаёт, но MeshCore room server может эхнуть наш
пост назад (R8) — поэтому держим «recently-TX» множество с TTL и сверяем входящие.
"""

from __future__ import annotations

import time
from typing import Callable

from .ttl_window import TtlWindow
from ..domain.models import Message


class LoopGuard:
    def __init__(self, ttl_seconds: float, *, _clock: Callable[[], float] = time.monotonic) -> None:
        self._window = TtlWindow(ttl_seconds, _clock=_clock)

    def mark_sent(self, text: str) -> None:
        """Запомнить полезную нагрузку, которую мы только что отправили в эфир."""
        self._window.add(text)

    def is_echo(self, msg: Message) -> bool:
        """``True`` — это эхо нашего недавнего TX (не пробрасываем в фан-аут)."""
        return msg.text in self._window
