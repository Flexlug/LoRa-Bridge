"""Сборка и маршрутизация: ingress fan-in, admission, фан-аут (§6, §12.1).

LoRa-путь (источник — нода): dedup + loop-guard → миррор в мессенджеры / relay в peer-LoRa.
Мессенджер-путь: admission в commit-очередь целевой ноды; post-commit миррор остальным.

Несколько нод: каждая = свой транспорт, своя commit-очередь и ОДИН egress-воркер (§6),
свои dedup/loop-guard и label-формат (политики ноды, §12).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace

import anyio

from ..domain.models import (
    ChannelRef,
    DeliveryStatus,
    LabelFormat,
    Message,
    RejectReason,
    Room,
)
from ..domain.ports import Transport
from .dedup import TtlDedup
from .egress import EgressWorker
from .journal import JournalEntry, OutboundJournal
from .loopguard import LoopGuard
from .notifier import DropNotifier
from .queue import CommitQueue, QueueItem
from .routing import LoraMember, MessengerMember, RoomRegistry
from .status import StatusDispatcher
from .transform import build_lora_text, oversize_bytes

log = logging.getLogger(__name__)


@dataclass
class NodeRuntime:
    """Per-node рантайм одной LoRa-ноды (всё, что радио-специфично, §12)."""
    transport: Transport
    queue: CommitQueue
    dedup: TtlDedup
    loop_guard: LoopGuard
    label_fmt: LabelFormat
    commit_timeout: float


class Bridge:
    def __init__(
        self,
        *,
        nodes: dict[str, NodeRuntime],        # LoRa-ноды по id (lora[].id)
        messengers: dict[str, Transport],     # мессенджеры по id
        tags: dict[str, str],                 # messenger_id → тег префикса ("TG")
        rooms: RoomRegistry,
        status: StatusDispatcher,
        notifier: DropNotifier,
        journal: OutboundJournal,
        notify_flush_interval: float = 30.0,
    ) -> None:
        self._nodes = nodes
        self._messengers = messengers
        self._tags = tags
        self._rooms = rooms
        self._status = status
        self._notifier = notifier
        self._journal = journal
        self._notify_flush_interval = notify_flush_interval

    # --- жизненный цикл -------------------------------------------------------

    async def run(self) -> None:
        async with anyio.create_task_group() as tg:
            for node in self._nodes.values():
                await node.transport.start()
            for messenger in self._messengers.values():
                await messenger.start()

            for t in (*[n.transport for n in self._nodes.values()], *self._messengers.values()):
                tg.start_soon(self._consume, t)
            for node_id, node in self._nodes.items():
                tg.start_soon(self._build_worker(node).run)
            tg.start_soon(self._notify_flush_loop)

    def _build_worker(self, node: NodeRuntime) -> EgressWorker:
        return EgressWorker(
            lora=node.transport,
            queue=node.queue,
            loop_guard=node.loop_guard,
            journal=self._journal,
            status=self._status,
            commit_timeout=node.commit_timeout,
            on_committed=self._on_committed,
            on_reject=self._on_reject,
        )

    async def _notify_flush_loop(self) -> None:
        while True:
            await anyio.sleep(self._notify_flush_interval)
            await self._notifier.flush_due()

    # --- ingress --------------------------------------------------------------

    async def _consume(self, t: Transport) -> None:
        node = self._nodes.get(t.id)
        async for msg in t.subscribe():
            if node is not None:                  # пришло из LoRa-ноды
                if not node.dedup.accept(msg):    # mesh-дубль (A3)
                    continue
                if node.loop_guard.is_echo(msg):  # собственное TX-эхо (A1/R8)
                    continue
                await self._route_from_lora(msg)
            else:                                 # пришло из мессенджера
                await self._admit(msg)

    async def _admit(self, msg: Message) -> None:
        """Мессенджер → commit-очередь целевой LoRa-ноды (§6)."""
        room = self._rooms.for_source(msg.source)
        if room is None:
            log.debug("сообщение из %s вне комнат — игнор", msg.source)
            return
        targets = [m for m in room.others(msg.source) if isinstance(m, LoraMember)]
        if not targets:                            # форма «1 LoRa + N msg» гарантирует один
            return
        await self._enqueue_to_lora(msg, targets[0], room, from_messenger=True)

    async def _route_from_lora(self, msg: Message) -> None:
        """RX из LoRa → остальным участникам (миррор в мессенджеры / relay в peer-LoRa, §12.1)."""
        room = self._rooms.for_source(msg.source)
        if room is None:
            return
        for member in room.others(msg.source):
            if isinstance(member, LoraMember):
                await self._enqueue_to_lora(msg, member, room, from_messenger=False)
            else:
                await self._mirror_to_messenger(member, msg)

    # --- egress (в LoRa) ------------------------------------------------------

    async def _enqueue_to_lora(
        self, src: Message, target: LoraMember, room, *, from_messenger: bool
    ) -> None:
        node = self._nodes[target.node_id]
        if from_messenger:
            tag = self._tags.get(src.source.transport_id, "?")
            droom = Room(target.endpoint, room.writable_messenger_count, target.node_id)
            text = build_lora_text(src, droom, tag, node.label_fmt)
        else:
            text = src.text                        # LoRa↔LoRa relay: форвардим как есть (§12.1)

        over = oversize_bytes(text, node.transport.capabilities.max_text_bytes)
        if over > 0:
            if from_messenger:
                await self._reject(src.source, src.id, RejectReason.TOO_LONG, f"+{over} Б")
            else:
                log.warning("relay %s→%s: текст не влез (+%d Б), drop", src.source, target.ref, over)
            return

        item = QueueItem(
            source=src.source, source_msg_id=src.id, target=target.ref,
            payload=replace(src, text=text), original=src, from_messenger=from_messenger,
        )
        if not node.queue.offer(item):             # bounded + rate-limit (§7)
            if from_messenger:
                await self._reject(src.source, src.id, RejectReason.RATE_LIMIT)
            else:
                log.warning("relay %s→%s: очередь полна, drop", src.source, target.ref)
            return

        await self._journal.record_pending(JournalEntry(
            msg_key=f"{src.source.transport_id}:{src.id}",
            origin_transport=src.source.transport_id, origin_chat=src.source.channel,
            origin_msg_id=src.id, target_node=target.node_id, target_endpoint=target.endpoint,
            status=DeliveryStatus.PENDING, enqueued_at=item.enqueued_at,
            tx_started_at=None, payload=text,
        ))
        await self._status.set(src.source, src.id, DeliveryStatus.PENDING)

    async def _on_committed(self, item: QueueItem) -> None:
        """После commit в LoRa — миррор оригинала остальным мессенджерам (§6, AD-4)."""
        if not item.from_messenger:
            return                                 # LoRa↔LoRa relay: мессенджеров нет
        room = self._rooms.for_source(item.source)
        if room is None:
            return
        for member in room.messenger_members():
            if member.ref != item.source:
                await self._mirror_to_messenger(member, item.original)

    async def _on_reject(self, item: QueueItem, reason: RejectReason) -> None:
        """TTL-протухание в очереди (вызывает egress)."""
        if item.from_messenger:
            await self._reject(item.source, item.source_msg_id, reason)
        else:
            log.warning("relay %s протух в очереди (%s), drop", item.source, reason.value)

    # --- миррор в мессенджеры -------------------------------------------------

    async def _mirror_to_messenger(self, member: MessengerMember, msg: Message) -> None:
        transport = self._messengers.get(member.transport_id)
        if transport is None:
            return
        try:
            await transport.send(member.ref, msg)  # best-effort, без статуса (A2)
        except Exception:                          # noqa: BLE001 — миррор не должен валить поток
            log.exception("миррор в %s не удался", member.ref)

    async def _reject(
        self, source: ChannelRef, msg_id: str, reason: RejectReason, detail: str = ""
    ) -> None:
        await self._status.set(source, msg_id, DeliveryStatus.REJECTED, reason)
        await self._notifier.note_reject(source, reason, detail)
