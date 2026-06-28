"""Debounced уведомления о дропах (B5).

Чтобы не спамить «rate limited» по каждому отброшенному сообщению — копим счётчики
по (source, reason) в окне и шлём одно агрегированное уведомление за окно.
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Awaitable, Callable, NamedTuple

import anyio

from ..domain.models import ChannelRef, RejectReason

# (chat-уведомитель) принимает source и текст; обычно — отправка в мессенджер-источник.
NotifySink = Callable[[ChannelRef, str], Awaitable[None]]


class WindowKey(NamedTuple):
    """Ключ окна дебаунса: один поток уведомлений на (транспорт, причина)."""

    transport_id: str
    reason: RejectReason


class DropKey(NamedTuple):
    """Ключ счётчика накопленных дропов: точный источник + причина."""

    source: ChannelRef
    reason: RejectReason

    @property
    def window_key(self) -> WindowKey:
        return WindowKey(self.source.transport_id, self.reason)


class DropNotifier:
    def __init__(
        self,
        window_seconds: float,
        sink: NotifySink,
        *,
        _clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window = window_seconds
        self._sink = sink
        self._clock = _clock
        self._counts: dict[DropKey, int] = defaultdict(int)
        self._last_flush: dict[WindowKey, float] = {}

    async def note_reject(self, source: ChannelRef, reason: RejectReason, detail: str = "") -> None:
        """Зарегистрировать отказ; первое за окно уведомляет сразу, дальше — копим."""
        now = self._clock()
        wkey = WindowKey(source.transport_id, reason)
        last = self._last_flush.get(wkey)
        if last is None or (now - last) >= self._window:
            # первое за окно — уведомляем немедленно, окно открывается
            self._last_flush[wkey] = now
            await self._sink(source, self.format_notice(reason, 1, detail))
        else:
            # внутри окна — просто накапливаем (хвост уйдёт следующим flush)
            self._counts[DropKey(source, reason)] += 1

    async def flush_due(self) -> None:
        """Слить накопленные хвосты по истёкшим окнам (зовётся периодически)."""
        now = self._clock()
        for drop_key, count in list(self._counts.items()):
            wkey = drop_key.window_key
            if (now - self._last_flush.get(wkey, 0)) >= self._window:
                self._last_flush[wkey] = now
                del self._counts[drop_key]
                await self._sink(
                    drop_key.source, self.format_notice(drop_key.reason, count, "")
                )

    async def run_flush_loop(self, interval: float = 30.0) -> None:
        """Периодически сбрасывать накопленные хвосты дропов (вызывается Supervisor'ом)."""
        while True:
            await anyio.sleep(interval)
            await self.flush_due()

    @staticmethod
    def format_notice(reason: RejectReason, count: int, detail: str) -> str:
        if reason == RejectReason.RATE_LIMIT:
            base = "эфир перегружен"
        elif reason == RejectReason.TOO_LONG:
            base = "сообщение слишком длинное"
        elif reason == RejectReason.TTL_EXPIRED:
            base = "сообщение устарело в очереди"
        else:
            raise ValueError(f"неизвестная причина: {reason!r}")
        suffix = f" ({detail})" if detail else ""
        if count > 1:
            return f"⚠️ {base}: отброшено {count} сообщений за окно"
        return f"⚠️ {base}{suffix}"
