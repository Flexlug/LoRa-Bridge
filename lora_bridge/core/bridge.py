"""Сборка и маршрутизация: ingress fan-in, admission, фан-аут (§6, §12.1).

LoRa-путь (источник — нода): dedup + loop-guard → миррор в мессенджеры / relay в peer-LoRa.
Мессенджер-путь: admission в commit-очередь целевой LoRa-ноды; post-commit миррор остальным.

Несколько нод: каждая = свой транспорт, своя commit-очередь и ОДИН egress-воркер (§6),
свои dedup/loop-guard, label-формат и нотификатор дропов (политики ноды, §12).
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, replace
from typing import Optional, assert_never

import anyio

from .dedup import TtlDedup
from .egress import RadioWorker
from .journal import JournalEntry, OutboundJournal
from .loopguard import LoopGuard
from .notifier import DropNotifier
from .queue import CommitQueue, QueueItem
from .routing import LoraMember, MessengerMember, RoomRegistry, RoomRoute
from .status import StatusDispatcher
from .supervisor import Supervisor
from .transform import build_lora_text, oversize_bytes, relay_lora_text
from ..domain.models import (
    ChannelRef,
    DeliveryStatus,
    LabelFormat,
    Message,
    RejectReason,
)
from ..domain.ports import AdmissionPolicy, Transport

log = logging.getLogger(__name__)

_NOTIFIER_FLUSH_INTERVAL = 30.0


@dataclass(frozen=True)
class MessengerBinding:
    """Мессенджер-транспорт + тег для форматирования меток (§10/AD-10)."""

    transport: Transport
    tag: str


@dataclass
class NodeRuntime:
    """Per-node рантайм одной LoRa-ноды (всё, что радио-специфично, §12)."""

    transport: Transport
    queue: CommitQueue
    dedup: TtlDedup
    loop_guard: LoopGuard
    label_fmt: LabelFormat
    commit_timeout: float
    notifier: DropNotifier  # per-node: своя политика окна уведомлений (P1 LoRa-Bridge-sew)


class Bridge:
    def __init__(
        self,
        *,
        nodes: dict[str, NodeRuntime],
        messengers: dict[str, MessengerBinding],
        rooms: RoomRegistry,
        status: StatusDispatcher,
        journal: OutboundJournal,
        admission_policy: Optional[AdmissionPolicy] = None,
    ) -> None:
        self._nodes = nodes
        self._messengers = messengers
        self._rooms = rooms
        self._status = status
        self._journal = journal
        self._admission_policy = admission_policy

    # --- жизненный цикл -------------------------------------------------------

    async def run(self) -> None:
        all_transports: list[Transport] = [
            *[node.transport for node in self._nodes.values()],
            *[binding.transport for binding in self._messengers.values()],
        ]
        for transport in all_transports:
            await transport.start()

        supervisor = Supervisor()
        for transport in all_transports:
            supervisor.register(f"consume:{transport.id}", functools.partial(self.consume, transport))
            supervisor.register(f"reconnect:{transport.id}", transport.run)
        for node_id, node in self._nodes.items():
            supervisor.register(f"egress:{node_id}", self.build_worker(node).run)
            supervisor.register(
                f"notifier:{node_id}",
                functools.partial(node.notifier.run_flush_loop, _NOTIFIER_FLUSH_INTERVAL),
            )

        try:
            await supervisor.run()
        finally:
            # shield: при graceful-выходе scope уже отменён, без него первый await
            # в stop() бросил бы Cancelled и оставил остальные транспорты незакрытыми.
            with anyio.CancelScope(shield=True):
                for transport in reversed(all_transports):
                    try:
                        await transport.stop()
                    except Exception:  # noqa: BLE001 — на shutdown не валим остальные stop()
                        log.exception("остановка транспорта '%s' упала", transport.id)

    def build_worker(self, node: NodeRuntime) -> RadioWorker:
        return RadioWorker(
            lora=node.transport,
            queue=node.queue,
            loop_guard=node.loop_guard,
            journal=self._journal,
            status=self._status,
            commit_timeout=node.commit_timeout,
            on_committed=self.on_committed,
            on_reject=self.on_reject,
        )

    # --- ingress --------------------------------------------------------------

    async def consume(self, transport: Transport) -> None:
        node = self._nodes.get(transport.id)
        async for msg in transport.subscribe():
            if node is not None:  # пришло из LoRa-ноды
                if not node.dedup.accept(msg):  # mesh-дубль (A3)
                    continue
                if node.loop_guard.is_echo(msg):  # собственное TX-эхо (A1/R8)
                    continue
                await self.route_from_lora(msg)
            else:  # пришло из мессенджера
                await self.admit(msg)

    async def admit(self, msg: Message) -> None:
        """Мессенджер → commit-очередь целевой LoRa-ноды (§6)."""
        room = self._rooms.for_source(msg.source)
        if room is None:
            log.debug("сообщение из %s вне комнат — игнор", msg.source)
            return
        targets = [m for m in room.others(msg.source) if isinstance(m, LoraMember)]
        if not targets:  # форма «1 LoRa + N msg» гарантирует один
            return
        target = targets[0]

        if self._admission_policy is not None:
            reason = await self._admission_policy.check(msg)
            if reason is not None:
                await self.reject(msg.source, msg.id, reason, node_id=target.node_id)
                return

        await self.enqueue_to_lora(msg, target, room, from_messenger=True)

    async def route_from_lora(self, msg: Message) -> None:
        """RX из LoRa → остальным участникам (миррор в мессенджеры / relay в peer-LoRa, §12.1)."""
        room = self._rooms.for_source(msg.source)
        if room is None:
            return
        for member in room.others(msg.source):
            match member:
                case LoraMember():
                    await self.enqueue_to_lora(msg, member, room, from_messenger=False)
                case MessengerMember():
                    await self.mirror_to_messenger(member, msg)
                case _ as unreachable:
                    assert_never(unreachable)

    # --- egress (в LoRa) ------------------------------------------------------

    async def enqueue_to_lora(
        self, src: Message, target: LoraMember, room: RoomRoute, *, from_messenger: bool
    ) -> None:
        node = self._nodes[target.node_id]
        if from_messenger:
            # KeyError здесь = нарушение инварианта wiring (каждый мессенджер имеет тег)
            tag = self._messengers[src.source.transport_id].tag
            text = build_lora_text(src, room.writable_messenger_count, tag, node.label_fmt)
        else:
            # LoRa↔LoRa relay: автор канала живёт в display_name (тело очищено
            # маппером), поэтому восстанавливаем его в wire-текст, иначе relay
            # потерял бы имя автора (§12.1).
            text = relay_lora_text(src)

        over = oversize_bytes(text, node.transport.capabilities.max_text_bytes)
        if over > 0:
            if from_messenger:
                await self.reject(src.source, src.id, RejectReason.TOO_LONG, f"+{over} Б",
                                  node_id=target.node_id)
            else:
                log.warning(
                    "relay %s→%s: текст не влез (+%d Б), drop: %r",
                    src.source, target.ref, over, src.text,
                )
            return

        item = QueueItem(
            source=src.source,
            source_msg_id=src.id,
            target=target.ref,
            payload=replace(src, text=text),
            original=src,
            from_messenger=from_messenger,
        )
        if not node.queue.put_nowait(item):  # bounded + rate-limit (§7)
            if from_messenger:
                await self.reject(src.source, src.id, RejectReason.RATE_LIMIT,
                                  node_id=target.node_id)
            else:
                log.warning("relay %s→%s: очередь полна, drop", src.source, target.ref)
            return

        await self._journal.record_pending(
            JournalEntry(
                msg_key=item.msg_key,
                origin_transport=src.source.transport_id,
                origin_chat=src.source.channel,
                origin_msg_id=src.id,
                target_node=target.node_id,
                target_endpoint=target.endpoint,
                status=DeliveryStatus.PENDING,
                enqueued_at=item.enqueued_at,
                tx_started_at=None,
                payload=text,
            )
        )
        await self._status.set(src.source, src.id, DeliveryStatus.PENDING)

    async def on_committed(self, item: QueueItem) -> None:
        """После commit в LoRa — миррор оригинала остальным мессенджерам (§6, AD-4)."""
        if not item.from_messenger:
            return  # LoRa↔LoRa relay: мессенджеров нет
        room = self._rooms.for_source(item.source)
        if room is None:
            return
        for member in room.messenger_members():
            if member.ref != item.source:
                await self.mirror_to_messenger(member, item.original)

    async def on_reject(self, item: QueueItem, reason: RejectReason) -> None:
        """TTL-протухание в очереди (вызывает egress)."""
        if item.from_messenger:
            await self.reject(item.source, item.source_msg_id, reason,
                              node_id=item.target.transport_id)
        else:
            log.warning("relay %s протух в очереди (%s), drop", item.source, reason)

    # --- миррор в мессенджеры -------------------------------------------------

    async def mirror_to_messenger(self, member: MessengerMember, msg: Message) -> None:
        binding = self._messengers.get(member.transport_id)
        if binding is None:
            return
        try:
            await binding.transport.send(member.ref, msg)  # best-effort, без статуса (A2)
        except Exception:  # noqa: BLE001 — миррор не должен валить поток
            log.exception("миррор в %s не удался", member.ref)

    async def reject(
        self,
        source: ChannelRef,
        msg_id: str,
        reason: RejectReason,
        detail: str = "",
        *,
        node_id: str,
    ) -> None:
        await self._status.set(source, msg_id, DeliveryStatus.REJECTED, reason)
        node = self._nodes.get(node_id)
        if node is not None:
            await node.notifier.note_reject(source, reason, detail)
