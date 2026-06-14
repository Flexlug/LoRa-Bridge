"""Debounced уведомления о дропах (B5).

Чтобы не спамить «rate limited» по каждому отброшенному сообщению — агрегируем
в одно уведомление за окно ``drop_notice_window_seconds`` на источник.
"""
from __future__ import annotations

from ..domain.models import ChannelRef, RejectReason


class DropNotifier:
    def __init__(self, window_seconds: float) -> None:
        self._window = window_seconds

    async def note_reject(
        self, source: ChannelRef, reason: RejectReason, detail: str = ""
    ) -> None:
        """Зарегистрировать отказ; внутри — debounce + агрегированное уведомление.

        TODO(B5): копить счётчики по (source, reason) в окне, по таймеру слать
        одно сообщение «N сообщений отброшено (RATE_LIMIT)…».
        """
        raise NotImplementedError("TODO(B5): debounce + агрегация")
