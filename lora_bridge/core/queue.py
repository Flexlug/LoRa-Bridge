"""Bounded commit-очередь + admission: rate-limit (token-bucket) + TTL (§6, §7).

Один узел физически сериализует TX → одна общая очередь и ОДИН egress-воркер.
Admission — единственное «доброе» место отказа: только здесь возвращаем REJECTED
с обратной связью (RATE_LIMIT / TTL_EXPIRED).
"""
from __future__ import annotations

from typing import AsyncIterator, Optional

from ..domain.models import Message, RateSpec


class CommitQueue:
    """FIFO-очередь намерений на отправку в LoRa.

    TODO(§6): bounded-буфер поверх anyio memory stream; token-bucket по RateSpec;
    per-source квота (B3); admission TTL (B1); пометка stale.
    """

    def __init__(self, capacity: int, rate: Optional[RateSpec], ttl_seconds: float) -> None:
        self._capacity = capacity
        self._rate = rate
        self._ttl = ttl_seconds

    def offer(self, msg: Message, payload: Message) -> bool:
        """Поставить в очередь. ``False`` → переполнение/лимит (вызывающий шлёт RATE_LIMIT)."""
        raise NotImplementedError("TODO(§6): bounded offer + token-bucket")

    def is_stale(self, msg: Message) -> bool:
        """Протухло ли по admission-TTL до отправки (B1)."""
        raise NotImplementedError("TODO(§6): TTL по enqueued_at")

    def __aiter__(self) -> AsyncIterator[tuple[Message, Message]]:
        """Поток ``(original_msg, payload)`` для egress-воркера."""
        raise NotImplementedError("TODO(§6): async-дренаж очереди")
