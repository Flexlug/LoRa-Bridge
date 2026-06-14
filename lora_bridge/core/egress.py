"""Единственный egress-воркер на ноду, gated на commit (§6).

Радиоузел передаёт по одному пакету за раз → ОДИН воркер на ноду. Сериализация
даёт ключевое свойство §11.1: в TRANSMITTING ≤ 1 сообщения в любой момент.
"""
from __future__ import annotations

from typing import Awaitable, Callable

import anyio

from ..domain.models import DeliveryStatus, RejectReason
from ..domain.ports import Transport
from .journal import OutboundJournal
from .loopguard import LoopGuard
from .queue import CommitQueue, QueueItem
from .status import StatusDispatcher

OnCommitted = Callable[[QueueItem], Awaitable[None]]
OnReject = Callable[[QueueItem, RejectReason], Awaitable[None]]

_BUSY_RETRIES = 3
_BUSY_BACKOFF_S = 1.0


class EgressWorker:
    def __init__(
        self,
        *,
        lora: Transport,
        queue: CommitQueue,
        loop_guard: LoopGuard,
        journal: OutboundJournal,
        status: StatusDispatcher,
        commit_timeout: float,
        on_committed: OnCommitted,
        on_reject: OnReject,
    ) -> None:
        self._lora = lora
        self._queue = queue
        self._loop_guard = loop_guard
        self._journal = journal
        self._status = status
        self._commit_timeout = commit_timeout
        self._on_committed = on_committed
        self._on_reject = on_reject

    @staticmethod
    def _key(item: QueueItem) -> str:
        return f"{item.source.transport_id}:{item.source_msg_id}"

    async def run(self) -> None:
        async for item in self._queue:
            if self._queue.is_stale(item):                 # протух по TTL до отправки (B1)
                await self._journal.mark_terminal(self._key(item), DeliveryStatus.REJECTED)
                await self._on_reject(item, RejectReason.TTL_EXPIRED)
                continue
            await self._transmit(item)

    async def _transmit(self, item: QueueItem) -> None:
        key = self._key(item)
        await self._journal.mark_transmitting(key)         # persist ДО node.send() (§11.1)
        await self._status.set(item.source, item.source_msg_id, DeliveryStatus.TRANSMITTING)

        result = await self._send_with_retry(item)

        if result is not None and result.ok:
            await self._journal.mark_terminal(key, DeliveryStatus.SENT)
            await self._status.set(item.source, item.source_msg_id, DeliveryStatus.SENT)
            self._loop_guard.mark_sent(item.payload.text)  # гасим обратное эхо (A1/R8)
            await self._on_committed(item)                 # post-commit миррор остальным (§6)
            await self._journal.prune(key)
        else:
            await self._journal.mark_terminal(key, DeliveryStatus.FAILED)
            await self._status.set(item.source, item.source_msg_id, DeliveryStatus.FAILED)

    async def _send_with_retry(self, item: QueueItem):
        """Отправка с таймаутом commit; ``busy`` (TABLE_FULL) → ретрай, не FAILED (R4)."""
        for attempt in range(_BUSY_RETRIES):
            try:
                with anyio.fail_after(self._commit_timeout):
                    res = await self._lora.send(item.target, item.payload)
            except TimeoutError:
                return None                                # нет commit в таймаут → FAILED (B2)
            if not res.busy:
                return res
            if attempt < _BUSY_RETRIES - 1:
                await anyio.sleep(_BUSY_BACKOFF_S)
        return res                                          # исчерпали ретраи busy → как есть
