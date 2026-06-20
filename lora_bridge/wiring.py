"""Фабрики: конфиг → граф объектов домена (§13).

Каждая функция знает об одном компоненте: как собрать транспорт, runtime ноды
или реестр комнат из конфига. run() в app.py только вызывает их по порядку.
"""

from __future__ import annotations

import logging
import uuid
from typing import NamedTuple, assert_never

from .config.schema import (
    AppConfig,
    LoraSubscriber,
    MessengerSubscriber,
    MeshCoreNode,
    MessengerConfig,
)
from .core.bridge import NodeRuntime
from .core.notifier import NotifySink
from .core.dedup import TtlDedup
from .core.loopguard import LoopGuard
from .core.queue import CommitQueue
from .core.routing import LoraMember, MessengerMember, RoomRegistry, RoomRoute
from .domain.models import ChannelRef, Identity, LabelFormat, Message, RateSpec
from .domain.ports import Transport
from .transports.meshcore.transport import MeshCoreTransport
from .transports.telegram.transport import TelegramTransport

log = logging.getLogger(__name__)

QUEUE_CAPACITY = 64


class LoraNodeEntry(NamedTuple):
    transport: Transport
    runtime: NodeRuntime


class MessengerEntry(NamedTuple):
    transport: Transport
    tag: str


class LoraNodes(NamedTuple):
    transports: dict[str, Transport]
    runtimes: dict[str, NodeRuntime]


class Messengers(NamedTuple):
    transports: dict[str, Transport]
    tags: dict[str, str]


def build_node(node: MeshCoreNode) -> LoraNodeEntry:
    log.debug("конструирую ноду %s (%s)", node.id, node.type)
    transport = MeshCoreTransport(node)
    p = node.policies
    rate = RateSpec(p.egress_rate.msgs_per_window, p.egress_rate.window_seconds)
    label = LabelFormat(
        include_type=p.label.include_type != "never",  # never→False; auto/always→True
        max_nick_bytes=p.label.max_nick_bytes,
    )
    runtime = NodeRuntime(
        transport=transport,
        queue=CommitQueue(QUEUE_CAPACITY, rate, p.queue_ttl_seconds),
        dedup=TtlDedup(p.dedup_ttl_seconds),
        loop_guard=LoopGuard(p.dedup_ttl_seconds),
        label_fmt=label,
        commit_timeout=p.commit_timeout_seconds,
    )
    return LoraNodeEntry(transport, runtime)


def build_messenger(cfg: MessengerConfig) -> MessengerEntry:
    log.debug("конструирую мессенджер %s (%s)", cfg.id, cfg.kind)
    tag = cfg.tag or cfg.kind.upper()[:2]
    return MessengerEntry(TelegramTransport(cfg.id, cfg), tag)


def build_rooms(config: AppConfig) -> RoomRegistry:
    routes: list[RoomRoute] = []
    for room in config.rooms:
        log.debug(
            "регистрирую комнату: %s/%s → %d подписчиков",
            room.lora.node,
            room.lora.endpoint,
            len(room.subscribers),
        )
        members: list = [LoraMember(room.lora.node, room.lora.endpoint)]
        for sub in room.subscribers:
            match sub:
                case LoraSubscriber():
                    members.append(LoraMember(sub.lora.node, sub.lora.endpoint))
                case MessengerSubscriber():
                    members.append(MessengerMember(sub.transport, sub.chat, sub.topic))
                case _ as unreachable:
                    assert_never(unreachable)
        routes.append(RoomRoute(members=tuple(members)))
    return RoomRegistry(routes)


def build_lora_nodes(config: AppConfig) -> LoraNodes:
    transports: dict[str, Transport] = {}
    runtimes: dict[str, NodeRuntime] = {}
    for node_cfg in config.lora:
        entry = build_node(node_cfg)
        transports[node_cfg.id] = entry.transport
        runtimes[node_cfg.id] = entry.runtime
        log.info("транспорт ноды '%s' создан (%d эндпоинтов)", node_cfg.id, len(node_cfg.endpoints))
    return LoraNodes(transports, runtimes)


def build_notice_sink(messenger_transports: dict[str, Transport], sender: Identity) -> NotifySink:
    async def notice_sink(ref: ChannelRef, text: str) -> None:
        transport = messenger_transports.get(ref.transport_id)
        if transport is None:
            return
        await transport.send(
            ref,
            Message(
                id=f"notice-{uuid.uuid4()}",
                source=ref,
                sender=sender,
                text=text,
            ),
        )

    return notice_sink


def build_messengers(config: AppConfig) -> Messengers:
    transports: dict[str, Transport] = {}
    tags: dict[str, str] = {}
    for m_cfg in config.messengers:
        entry = build_messenger(m_cfg)
        transports[m_cfg.id] = entry.transport
        tags[m_cfg.id] = entry.tag
        log.info("транспорт мессенджера '%s' создан (тег: %s)", m_cfg.id, entry.tag)
    return Messengers(transports, tags)
