"""Composition root: собрать граф объектов из конфига и запустить мост (§13).

Здесь — и ТОЛЬКО здесь — связываются слои: читаем конфиг, инстанцируем транспорты
по ``node.type`` / ``messenger.kind``, собираем ядро, делаем recovery журнала (§11.1),
отдаём управление ``Bridge.run``.
"""
from __future__ import annotations

import logging
import os

import anyio

from .config.loader import load_config
from .config.schema import (
    AppConfig,
    LoraSubscriber,
    MessengerConfig,
    MeshCoreNode,
)
from .core.bridge import Bridge, NodeRuntime
from .core.dedup import TtlDedup
from .core.journal import SqliteJournal
from .core.loopguard import LoopGuard
from .core.notifier import DropNotifier
from .core.queue import CommitQueue, QueueItem
from .core.routing import LoraMember, MessengerMember, RoomRegistry, RoomRoute
from .core.status import StatusDispatcher
from .domain.models import (
    ChannelRef,
    DeliveryStatus,
    Identity,
    LabelFormat,
    Message,
    RateSpec,
)
from .domain.ports import Transport
from .transports.meshcore.transport import MeshCoreTransport
from .transports.telegram.transport import TelegramTransport

log = logging.getLogger(__name__)

_QUEUE_CAPACITY = 64
_NOTICE_SENDER = Identity(display_name="bridge", transport_uid="__bridge__")


def _build_node(node: MeshCoreNode) -> tuple[Transport, NodeRuntime]:
    if node.type != "meshcore":                       # discriminated union point (§12)
        raise ValueError(f"нода {node.id}: тип '{node.type}' пока не поддержан")
    transport = MeshCoreTransport(node)
    p = node.policies
    rate = RateSpec(p.egress_rate.msgs_per_window, p.egress_rate.window_seconds)
    label = LabelFormat(
        include_type=p.label.include_type != "never",  # never→False; auto/always→True
        template=p.label.format,
        max_nick_bytes=p.label.max_nick_bytes,
    )
    runtime = NodeRuntime(
        transport=transport,
        queue=CommitQueue(_QUEUE_CAPACITY, rate, p.queue_ttl_seconds),
        dedup=TtlDedup(p.dedup_ttl_seconds),
        loop_guard=LoopGuard(p.dedup_ttl_seconds),
        label_fmt=label,
        commit_timeout=p.commit_timeout_seconds,
    )
    return transport, runtime


def _build_messenger(cfg: MessengerConfig) -> tuple[Transport, str]:
    tag = cfg.tag or cfg.kind.upper()[:2]
    if cfg.kind != "telegram":
        raise ValueError(f"мессенджер {cfg.id}: kind '{cfg.kind}' пока не поддержан")
    return TelegramTransport(cfg.id, tag, cfg), tag


def _build_rooms(config: AppConfig) -> RoomRegistry:
    routes: list[RoomRoute] = []
    for room in config.rooms:
        members: list = [LoraMember(room.lora.node, room.lora.endpoint)]
        for sub in room.subscribers:
            if isinstance(sub, LoraSubscriber):
                members.append(LoraMember(sub.lora.node, sub.lora.endpoint))
            else:
                members.append(MessengerMember(sub.transport, sub.chat, sub.topic))
        routes.append(RoomRoute(members=tuple(members)))
    return RoomRegistry(routes)


async def _recover(journal: SqliteJournal, nodes: dict[str, NodeRuntime],
                   status: StatusDispatcher, messenger_ids: set[str]) -> None:
    """Починка сирот после рестарта (§11.1): TRANSMITTING→UNKNOWN, PENDING→re-enqueue."""
    for e in await journal.recover():
        origin = ChannelRef(e.origin_transport, e.origin_chat)
        if e.status == DeliveryStatus.TRANSMITTING:
            # ушло ли в эфир — неизвестно; авто-ретрая НЕТ (§11.1)
            await status.set(origin, e.origin_msg_id, DeliveryStatus.UNKNOWN)
            await journal.mark_terminal(e.msg_key, DeliveryStatus.UNKNOWN)
            continue
        node = nodes.get(e.target_node)               # PENDING → не ушло, ре-энкью
        if node is None:
            continue
        payload = Message(id=e.origin_msg_id, source=origin,
                          sender=_NOTICE_SENDER, text=e.payload)
        node.queue.offer(QueueItem(
            source=origin, source_msg_id=e.origin_msg_id, target=ChannelRef(e.target_node, e.target_endpoint),
            payload=payload, original=payload, from_messenger=e.origin_transport in messenger_ids,
        ))
        await status.set(origin, e.origin_msg_id, DeliveryStatus.PENDING)


async def run(config: AppConfig) -> None:
    """Собрать и запустить мост."""
    nodes: dict[str, NodeRuntime] = {}
    node_transports: dict[str, Transport] = {}
    for node_cfg in config.lora:
        transport, runtime = _build_node(node_cfg)
        nodes[node_cfg.id] = runtime
        node_transports[node_cfg.id] = transport

    messengers: dict[str, Transport] = {}
    tags: dict[str, str] = {}
    for m_cfg in config.messengers:
        messengers[m_cfg.id], tags[m_cfg.id] = _build_messenger(m_cfg)

    all_transports: dict[str, Transport] = {**node_transports, **messengers}
    status = StatusDispatcher(all_transports)

    async def notice_sink(ref: ChannelRef, text: str) -> None:
        transport = messengers.get(ref.transport_id)
        if transport is None:
            return
        await transport.send(ref, Message(id=f"notice-{id(text)}", source=ref,
                                          sender=_NOTICE_SENDER, text=text))

    notifier = DropNotifier(config.lora[0].policies.drop_notice_window_seconds, notice_sink)

    journal = SqliteJournal(os.environ.get("LORA_BRIDGE_DB", "lora_bridge.sqlite"))
    await journal.start()
    await _recover(journal, nodes, status, set(messengers))

    bridge = Bridge(
        nodes=nodes, messengers=messengers, tags=tags,
        rooms=_build_rooms(config), status=status, notifier=notifier, journal=journal,
    )
    try:
        await bridge.run()
    finally:
        await journal.stop()


def main() -> None:
    """CLI-точка входа (см. [project.scripts] в pyproject.toml)."""
    logging.basicConfig(level=os.environ.get("LORA_BRIDGE_LOG", "INFO"))
    config_path = os.environ.get("LORA_BRIDGE_CONFIG", "config.yaml")
    config = load_config(config_path)
    anyio.run(run, config)


if __name__ == "__main__":
    main()
