"""Единственный egress-воркер, gated на commit (§6).

Радиоузел передаёт по одному пакету за раз → ОДИН воркер на узел. Сериализация
даёт ключевое свойство §11.1: в TRANSMITTING ≤ 1 сообщения в любой момент.
"""
from __future__ import annotations

from ..domain.models import DeliveryStatus, RejectReason, SendResult
from ..domain.ports import Transport
from .queue import CommitQueue


class EgressWorker:
    def __init__(
        self,
        lora: Transport,
        queue: CommitQueue,
        commit_timeout: float,
    ) -> None:
        self._lora = lora
        self._queue = queue
        self._commit_timeout = commit_timeout

    async def run(self) -> None:
        """Дренируем очередь, отдаём узлу, резолвим по commit/таймауту.

        TODO(§6): for msg, payload in queue:
          - is_stale → REJECTED(TTL_EXPIRED)
          - journal.mark_transmitting ДО send (§11.1)
          - status=TRANSMITTING; res = with_timeout(lora.send(...), commit_timeout)
          - res.ok → SENT + fan-out (exclude источник); res.busy → re-enqueue;
            иначе/таймаут → FAILED (B2)
        """
        raise NotImplementedError("TODO(§6): egress-цикл")

    @staticmethod
    def _classify(res: SendResult) -> DeliveryStatus:
        if res.ok:
            return DeliveryStatus.SENT
        # busy обрабатывается отдельно (re-enqueue), сюда попадает как FAILED-исход
        return DeliveryStatus.FAILED

    # подсказка для линтеров о неиспользуемом импорте на этапе скелета
    _REJECT_TTL = RejectReason.TTL_EXPIRED
