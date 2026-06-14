"""Debounced уведомления о дропах (B5).

Чтобы не спамить «rate limited» по каждому отброшенному сообщению — копим счётчики
по (source, reason) в окне и шлём одно агрегированное уведомление за окно.
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Awaitable, Callable

from ..domain.models import ChannelRef, RejectReason

# (chat-уведомитель) принимает source и текст; обычно — отправка в мессенджер-источник.
NotifySink = Callable[[ChannelRef, str], Awaitable[None]]


class DropNotifier:
    def __init__(
        self,
        window_seconds: float,
        sink: NotifySink,
        *,
        _clock=time.monotonic,
    ) -> None:
        self._window = window_seconds
        self._sink = sink
        self._clock = _clock
        self._counts: dict[tuple[str, str, RejectReason], int] = defaultdict(int)
        self._last_flush: dict[tuple[str, RejectReason], float] = {}

    async def note_reject(
        self, source: ChannelRef, reason: RejectReason, detail: str = ""
    ) -> None:
        """Зарегистрировать отказ; первое за окно уведомляет сразу, дальше — копим."""
        now = self._clock()
        flush_key = (source.transport_id, reason)
        last = self._last_flush.get(flush_key)
        if last is None or (now - last) >= self._window:
            # первое за окно — уведомляем немедленно, окно открывается
            self._last_flush[flush_key] = now
            await self._sink(source, self._format(reason, 1, detail))
        else:
            # внутри окна — просто накапливаем (хвост уйдёт следующим flush)
            self._counts[(source.transport_id, source.channel, reason)] += 1

    async def flush_due(self) -> None:
        """Слить накопленные хвосты по истёкшим окнам (зовётся периодически)."""
        now = self._clock()
        for (tid, channel, reason), count in list(self._counts.items()):
            flush_key = (tid, reason)
            if (now - self._last_flush.get(flush_key, 0)) >= self._window:
                self._last_flush[flush_key] = now
                del self._counts[(tid, channel, reason)]
                if count:
                    await self._sink(ChannelRef(tid, channel), self._format(reason, count, ""))

    @staticmethod
    def _format(reason: RejectReason, count: int, detail: str) -> str:
        base = {
            RejectReason.RATE_LIMIT: "эфир перегружен",
            RejectReason.TOO_LONG: "сообщение слишком длинное",
            RejectReason.TTL_EXPIRED: "сообщение устарело в очереди",
        }[reason]
        suffix = f" ({detail})" if detail else ""
        if count > 1:
            return f"⚠️ {base}: отброшено {count} сообщений за окно"
        return f"⚠️ {base}{suffix}"
