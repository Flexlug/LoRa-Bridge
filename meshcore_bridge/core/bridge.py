"""Сборка и маршрутизация: ingress fan-in, admission, фан-аут (§6).

LoRa-путь: dedup + loop-guard → зеркалим в мессенджеры.
Мессенджер-путь: admission в commit-очередь (TOO_LONG/RATE_LIMIT синхронно).
"""
from __future__ import annotations

from dataclasses import replace

from ..domain.models import (
    DeliveryStatus,
    LabelFormat,
    Message,
    RejectReason,
    Room,
)
from ..domain.ports import Transport
from .dedup import TtlDedup
from .loopguard import LoopGuard
from .notifier import DropNotifier
from .queue import CommitQueue
from .status import StatusDispatcher
from .transform import build_lora_text, oversize_bytes


class Bridge:
    def __init__(
        self,
        *,
        lora: Transport,
        messengers: dict[str, Transport],
        rooms: "RoomRegistry",
        queue: CommitQueue,
        dedup: TtlDedup,
        loop_guard: LoopGuard,
        status: StatusDispatcher,
        notifier: DropNotifier,
        label_fmt: LabelFormat,
    ) -> None:
        self._lora = lora
        self._messengers = messengers
        self._rooms = rooms
        self._queue = queue
        self._dedup = dedup
        self._loop_guard = loop_guard
        self._status = status
        self._notifier = notifier
        self._label_fmt = label_fmt

    async def run(self) -> None:
        """Старт транспортов + потребители + egress-воркер (§6).

        TODO(§6): anyio.create_task_group: start() всех транспортов,
        start_soon(_consume, t) на каждый, start_soon egress-воркера (один на узел).
        """
        raise NotImplementedError("TODO(§6): task group / supervisor")

    async def _consume(self, t: Transport) -> None:
        async for msg in t.subscribe():
            if self._is_lora_origin(msg):
                # dedup и loop-guard — ТОЛЬКО для LoRa-пути (A1/A3).
                if not self._dedup.accept(msg):
                    continue
                if self._loop_guard.is_echo(msg):
                    continue
                await self._fanout_to_messengers(msg, exclude=None)
            else:
                await self._admit(msg)

    async def _admit(self, msg: Message) -> None:
        room = self._rooms.for_source(msg.source)
        tag = self._tag_of(msg.source.transport_id)
        text = build_lora_text(msg, room, tag, self._label_fmt)

        # AD-11: НЕ усекаем текст. Не влезло — сразу разворачиваем обратно с ошибкой.
        over = oversize_bytes(text, self._lora.capabilities.max_text_bytes)
        if over > 0:
            await self._reject(msg, RejectReason.TOO_LONG, detail=f"+{over} Б")
            return

        out = replace(msg, text=text)             # та же модель, готовый payload
        if not self._queue.offer(msg, out):        # bounded + rate-limit → RATE_LIMIT
            await self._reject(msg, RejectReason.RATE_LIMIT)
            return
        await self._status.set(msg.source, msg.id, DeliveryStatus.PENDING)

    async def _reject(self, msg: Message, reason: RejectReason, detail: str = "") -> None:
        await self._status.set(msg.source, msg.id, DeliveryStatus.REJECTED, reason)
        await self._notifier.note_reject(msg.source, reason, detail)   # debounce

    async def _fanout_to_messengers(self, msg: Message, exclude: str | None) -> None:
        """Зеркалим сообщение во все мессенджеры комнаты, кроме источника (A2).

        TODO(§6): по room.subscribers вызвать messenger.send(target, msg);
        best-effort, без статуса для зеркал.
        """
        raise NotImplementedError("TODO(§6): mirror в подписчиков комнаты")

    def _is_lora_origin(self, msg: Message) -> bool:
        return msg.source.transport_id == self._lora.id

    def _tag_of(self, transport_id: str) -> str:
        # TODO(§4/D5): тег из конфига транспорта (messengers[].tag/kind), не из текста.
        raise NotImplementedError("TODO(§4): резолв тега транспорта")


class RoomRegistry:
    """Маппинг ChannelRef-источника → ``Room`` (комнаты из конфига, §12).

    TODO(§12): построить из config.rooms; ``for_source`` отдаёт комнату по
    source-эндпоинту (мессенджер-чат/топик или LoRa-эндпоинт).
    """

    def for_source(self, source) -> Room:  # noqa: ANN001
        raise NotImplementedError("TODO(§12): резолв комнаты по источнику")
