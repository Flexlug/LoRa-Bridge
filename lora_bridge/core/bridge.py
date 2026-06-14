"""Сборка и маршрутизация: ingress fan-in, admission, фан-аут (§6).

LoRa-путь: dedup + loop-guard → зеркалим в мессенджеры.
Мессенджер-путь: admission в commit-очередь нужной ноды (TOO_LONG/RATE_LIMIT синхронно).

Несколько физических LoRa-нод: каждая нода = свой транспорт, своя commit-очередь и
ОДИН egress-воркер (§6), свои dedup/loop-guard и label-формат (политики ноды, §12).
"""
from __future__ import annotations

from dataclasses import dataclass, replace

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


@dataclass
class NodeRuntime:
    """Per-node рантайм одной LoRa-ноды (всё, что радио-специфично, §12)."""
    transport: Transport
    queue: CommitQueue
    dedup: TtlDedup
    loop_guard: LoopGuard
    label_fmt: LabelFormat


class Bridge:
    def __init__(
        self,
        *,
        nodes: dict[str, NodeRuntime],        # LoRa-ноды по id (lora[].id)
        messengers: dict[str, Transport],
        rooms: "RoomRegistry",
        status: StatusDispatcher,
        notifier: DropNotifier,
    ) -> None:
        self._nodes = nodes
        self._messengers = messengers
        self._rooms = rooms
        self._status = status
        self._notifier = notifier

    async def run(self) -> None:
        """Старт транспортов + потребители + egress-воркеры (§6).

        TODO(§6): anyio.create_task_group: start() всех транспортов (ноды + мессенджеры),
        start_soon(_consume, t) на каждый, и start_soon egress-воркера НА КАЖДУЮ ноду.
        """
        raise NotImplementedError("TODO(§6): task group / supervisor (per-node egress)")

    async def _consume(self, t: Transport) -> None:
        node = self._nodes.get(t.id)
        async for msg in t.subscribe():
            if node is not None:                  # пришло из LoRa-ноды
                # dedup и loop-guard — ТОЛЬКО для LoRa-пути, и per-node (A1/A3).
                if not node.dedup.accept(msg):
                    continue
                if node.loop_guard.is_echo(msg):
                    continue
                await self._fanout_to_messengers(msg, exclude=None)
            else:                                 # пришло из мессенджера
                await self._admit(msg)

    async def _admit(self, msg: Message) -> None:
        room = self._rooms.for_source(msg.source)
        node = self._nodes[room.node_id]          # целевая нода комнаты
        tag = self._tag_of(msg.source.transport_id)
        text = build_lora_text(msg, room, tag, node.label_fmt)

        # AD-11: НЕ усекаем текст. Не влезло — сразу разворачиваем обратно с ошибкой.
        over = oversize_bytes(text, node.transport.capabilities.max_text_bytes)
        if over > 0:
            await self._reject(msg, RejectReason.TOO_LONG, detail=f"+{over} Б")
            return

        out = replace(msg, text=text)             # та же модель, готовый payload
        if not node.queue.offer(msg, out):         # bounded + rate-limit → RATE_LIMIT
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

    def _tag_of(self, transport_id: str) -> str:
        # TODO(§4/D5): тег из конфига транспорта (messengers[].tag/kind), не из текста.
        raise NotImplementedError("TODO(§4): резолв тега транспорта")


class RoomRegistry:
    """Маппинг ChannelRef-источника → ``Room`` (комнаты из конфига, §12).

    TODO(§12): построить из config.rooms; ``for_source`` отдаёт комнату по
    source-эндпоинту (мессенджер-чат/топик или LoRa node+endpoint).
    """

    def for_source(self, source) -> Room:  # noqa: ANN001
        raise NotImplementedError("TODO(§12): резолв комнаты по источнику")
