"""Мапер channel-эндпоинтов MeshCore (public / private).

Commit = MSG_OK (flood, без реальной доставки). Специфика типа: слот-индекс канала
на устройстве, деривация PSK (public → sha256(name)[:16] внутри meshcore;
private → raw hex PSK из приложения), запись канала в свободный слот.
RX-событие — CHANNEL_MSG_RECV (имя отправителя не несёт, callsign уже в тексте).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any, assert_never

from ....domain.models import (
    ChannelRef,
    Identity,
    LORA_SENDER_UID,
    Message,
)

log = logging.getLogger(__name__)


@dataclass
class PublicEndpointState:
    name: str
    channel_name: str
    channel_index: int | None = None  # резолвится в start()


@dataclass
class PrivateEndpointState:
    name: str
    channel_name: str
    secret: str
    channel_index: int | None = None  # резолвится в start()


ChannelEndpointState = PublicEndpointState | PrivateEndpointState


def channel_secret_bytes(ep: ChannelEndpointState) -> bytes | None:
    match ep:
        case PublicEndpointState():
            return None  # PSK = sha256(name)[:16] внутри meshcore
        case PrivateEndpointState():
            return bytes.fromhex(ep.secret)  # секрет — raw hex PSK из MeshCore-приложения
        case _ as unreachable:
            assert_never(unreachable)


def expected_channel_hash(ep: ChannelEndpointState) -> str:
    secret = channel_secret_bytes(ep)
    if secret is None:
        secret = hashlib.sha256(ep.channel_name.encode()).digest()[:16]
    return hashlib.sha256(secret).hexdigest()[:2]


async def resolve_channel(
    mc: Any,
    ep: ChannelEndpointState,
    node_id: str,
    *,
    configured_channel_names: set[str],
    override_oldest: bool,
) -> int:
    """Найти канал на устройстве по имени.

    Если найден, но PSK не совпадает с конфигом — перезаписывает слот.
    Если не найден — создаёт в первом свободном слоте.
    """
    device_info = await mc.commands.send_device_query()
    if device_info.is_error():
        raise RuntimeError(f"нода '{node_id}': не удалось получить device info")
    max_channels = device_info.payload.get("max_channels", 8)

    expected_hash = expected_channel_hash(ep)

    found: list[str] = []
    first_empty: int | None = None
    psk_mismatch_idx: int | None = None
    foreign_slots: list[tuple[int, str]] = []  # слоты с каналами не из нашего конфига
    for idx in range(max_channels):
        ch = await mc.commands.get_channel(idx)
        if ch.is_error():
            break
        name = ch.payload.get("channel_name", "")
        ch_hash = ch.payload.get("channel_hash", "?")
        found.append(name)
        if name:
            log.debug("нода '%s': слот %d: '%s' chan_hash=%s", node_id, idx, name, ch_hash)
        if name == ep.channel_name:
            if ch_hash == expected_hash:
                log.debug("нода '%s': канал '%s' → слот %d (chan_hash=%s)", node_id, ep.channel_name, idx, ch_hash)
                return idx
            log.warning(
                "нода '%s': канал '%s' в слоте %d имеет chan_hash=%s, ожидается %s — перезапишу",
                node_id, ep.channel_name, idx, ch_hash, expected_hash,
            )
            psk_mismatch_idx = idx
        elif name == "":
            if first_empty is None:
                first_empty = idx
        elif name not in configured_channel_names:
            foreign_slots.append((idx, name))

    if psk_mismatch_idx is not None:
        return await create_channel(mc, ep, psk_mismatch_idx, node_id)

    non_empty = [n for n in found if n]
    log.debug("нода '%s': каналы на устройстве: %s", node_id, non_empty)

    if first_empty is None:
        if override_oldest and foreign_slots:
            override_idx, override_name = foreign_slots[0]
            log.warning(
                "нода '%s': нет свободных слотов — вытесняю слот %d ('%s')",
                node_id, override_idx, override_name,
            )
            return await create_channel(mc, ep, override_idx, node_id)
        raise RuntimeError(
            f"нода '{node_id}': канал '{ep.channel_name}' не найден "
            f"и нет свободных слотов. Каналы на устройстве: {non_empty}"
        )

    return await create_channel(mc, ep, first_empty, node_id)


async def create_channel(mc: Any, ep: ChannelEndpointState, slot: int, node_id: str) -> int:
    """Записать канал в пустой слот через set_channel.

    Запись идёт во flash MCU — устройство может на несколько секунд
    перестать реагировать на кнопки и затем перезагрузиться.
    Это нормальное поведение: реконнект-цикл подхватит его автоматически,
    после чего канал будет найден и бот продолжит работу.
    """
    secret_bytes = channel_secret_bytes(ep)

    log.warning(
        "нода '%s': канал '%s' не найден — записываю в слот %d. "
        "Устройство может перезапуститься, бот переподключится автоматически.",
        node_id, ep.channel_name, slot,
    )
    log.debug(
        "нода '%s': вызов set_channel(slot=%d, name=%r, secret=%s)",
        node_id, slot, ep.channel_name,
        "auto" if secret_bytes is None else secret_bytes.hex(),
    )
    res = await mc.commands.set_channel(slot, ep.channel_name, secret_bytes)  # verify
    log.debug("нода '%s': ответ set_channel: type=%s payload=%s", node_id, res.type, res.payload)
    if res.is_error():
        raise RuntimeError(
            f"нода '{node_id}': не удалось записать канал '{ep.channel_name}': {res.payload}"
        )
    log.info("нода '%s': канал '%s' записан в слот %d", node_id, ep.channel_name, slot)
    return slot


async def send_channel(mc: Any, ep: ChannelEndpointState, text: str, node_id: str) -> Any:
    log.debug(
        "нода '%s': send_chan_msg слот=%s текст=%r",
        node_id, ep.channel_index, text,
    )
    return await mc.commands.send_chan_msg(ep.channel_index, text)


def channel_to_message(payload: dict[str, Any], endpoint: str, node_id: str) -> Message:
    ts = payload.get("sender_timestamp", 0)
    text = payload.get("text", "")
    # CHANNEL_MSG_RECV не несёт имени отправителя — callsign уже в тексте
    return Message(
        id=f"{endpoint}:{ts}:{hash(text)}",
        source=ChannelRef(node_id, endpoint),
        sender=Identity(display_name="", transport_uid=LORA_SENDER_UID),
        text=text,
    )
