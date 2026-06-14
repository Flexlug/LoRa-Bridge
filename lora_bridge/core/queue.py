"""Bounded commit-очередь + admission: rate-limit (token-bucket) + TTL (§6, §7).

Один узел физически сериализует TX → одна очередь и ОДИН egress-воркер на ноду.
Admission — единственное «доброе» место отказа: только здесь возвращаем REJECTED
с обратной связью (RATE_LIMIT / TTL_EXPIRED).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from .ratelimit import TokenBucket
from ..domain.models import ChannelRef, Message, RateSpec


@dataclass
class QueueItem:
    """Намерение передать payload на конкретный LoRa-эндпоинт (§6)."""

    source: ChannelRef  # откуда пришло (для статусов/мирроринга)
    source_msg_id: str  # native id источника (корреляция статуса/журнала)
    target: ChannelRef  # целевой LoRa-эндпоинт (node/endpoint)
    payload: Message  # уже собранная строка [тип:ник]+текст
    original: Message  # исходное сообщение (для post-commit миррора)
    from_messenger: bool  # источник — мессенджер (нужен статус/миррор)
    enqueued_at: float = field(default_factory=time.monotonic)


class CommitQueue:
    def __init__(
        self,
        capacity: int,
        rate: Optional[RateSpec],
        ttl_seconds: float,
        *,
        _clock=time.monotonic,
    ) -> None:
        self._send: MemoryObjectSendStream[QueueItem]
        self._recv: MemoryObjectReceiveStream[QueueItem]
        self._send, self._recv = anyio.create_memory_object_stream[QueueItem](capacity)
        self._bucket = TokenBucket(rate, _clock=_clock) if rate else None
        self._ttl = ttl_seconds
        self._clock = _clock

    def offer(self, item: QueueItem) -> bool:
        """Поставить в очередь. ``False`` → переполнение/лимит → вызывающий шлёт RATE_LIMIT."""
        if self._bucket is not None and not self._bucket.try_consume():
            return False
        try:
            self._send.send_nowait(item)
            return True
        except anyio.WouldBlock:
            return False

    def is_stale(self, item: QueueItem) -> bool:
        """Протухло ли по admission-TTL до отправки (B1)."""
        return (self._clock() - item.enqueued_at) > self._ttl

    def __aiter__(self) -> AsyncIterator[QueueItem]:
        return self._recv.__aiter__()

    async def close_input(self) -> None:
        """Закрыть вход: воркер дренирует буфер и завершает async-for (для shutdown/тестов)."""
        await self._send.aclose()

    async def aclose(self) -> None:
        await self._send.aclose()
        await self._recv.aclose()
