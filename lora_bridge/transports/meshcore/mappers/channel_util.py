"""Общие функции channel-эндпоинтов MeshCore (public и private).

Здесь живёт только логика, одинаковая для обоих типов каналов: резолв слота на
устройстве, запись канала в слот, отправка и нормализация RX в доменный
``Message``. Различие public/private сводится к одному параметру ``secret_bytes``
(``None`` → PSK = sha256(name)[:16] внутри meshcore; иначе — raw hex PSK), который
передаёт конкретный хэндлер. SRP: типы каналов — в ``public``/``private``.

Commit = MSG_OK (flood, без реальной доставки). RX-событие — CHANNEL_MSG_RECV
(имя отправителя не несёт, callsign уже в тексте). Точные вызовы ``meshcore_py``
помечены ``# verify``.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from meshcore import MeshCore

from ....domain.models import (
    ChannelRef,
    Identity,
    LORA_SENDER_UID,
    Message,
)

log = logging.getLogger(__name__)


def expected_channel_hash(channel_name: str, secret_bytes: bytes | None) -> str:
    if secret_bytes is None:
        secret_bytes = hashlib.sha256(channel_name.encode()).digest()[:16]
    return hashlib.sha256(secret_bytes).hexdigest()[:2]


async def resolve_channel(
    mc: MeshCore,
    *,
    channel_name: str,
    secret_bytes: bytes | None,
    node_id: str,
    configured_channel_names: frozenset[str],
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

    expected_hash = expected_channel_hash(channel_name, secret_bytes)

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
        if name == channel_name:
            if ch_hash == expected_hash:
                log.debug("нода '%s': канал '%s' → слот %d (chan_hash=%s)", node_id, channel_name, idx, ch_hash)
                return idx
            log.warning(
                "нода '%s': канал '%s' в слоте %d имеет chan_hash=%s, ожидается %s — перезапишу",
                node_id, channel_name, idx, ch_hash, expected_hash,
            )
            psk_mismatch_idx = idx
        elif name == "":
            if first_empty is None:
                first_empty = idx
        elif name not in configured_channel_names:
            foreign_slots.append((idx, name))

    if psk_mismatch_idx is not None:
        return await create_channel(
            mc, channel_name=channel_name, secret_bytes=secret_bytes,
            slot=psk_mismatch_idx, node_id=node_id,
        )

    non_empty = [n for n in found if n]
    log.debug("нода '%s': каналы на устройстве: %s", node_id, non_empty)

    if first_empty is None:
        if override_oldest and foreign_slots:
            override_idx, override_name = foreign_slots[0]
            log.warning(
                "нода '%s': нет свободных слотов — вытесняю слот %d ('%s')",
                node_id, override_idx, override_name,
            )
            return await create_channel(
                mc, channel_name=channel_name, secret_bytes=secret_bytes,
                slot=override_idx, node_id=node_id,
            )
        raise RuntimeError(
            f"нода '{node_id}': канал '{channel_name}' не найден "
            f"и нет свободных слотов. Каналы на устройстве: {non_empty}"
        )

    return await create_channel(
        mc, channel_name=channel_name, secret_bytes=secret_bytes,
        slot=first_empty, node_id=node_id,
    )


async def create_channel(
    mc: MeshCore, *, channel_name: str, secret_bytes: bytes | None, slot: int, node_id: str
) -> int:
    """Записать канал в пустой слот через set_channel.

    Запись идёт во flash MCU — устройство может на несколько секунд
    перестать реагировать на кнопки и затем перезагрузиться.
    Это нормальное поведение: реконнект-цикл подхватит его автоматически,
    после чего канал будет найден и бот продолжит работу.
    """
    log.warning(
        "нода '%s': канал '%s' не найден — записываю в слот %d. "
        "Устройство может перезапуститься, бот переподключится автоматически.",
        node_id, channel_name, slot,
    )
    log.debug(
        "нода '%s': вызов set_channel(slot=%d, name=%r, secret=%s)",
        node_id, slot, channel_name,
        "auto" if secret_bytes is None else secret_bytes.hex(),
    )
    # secret_bytes=None валиден (auto-PSK из имени), но либа аннотировала параметр как
    # bytes, а не Optional[bytes] — подавляем неточность типа на стыке.
    res = await mc.commands.set_channel(slot, channel_name, secret_bytes)  # type: ignore[arg-type]  # verify
    log.debug("нода '%s': ответ set_channel: type=%s payload=%s", node_id, res.type, res.payload)
    if res.is_error():
        raise RuntimeError(
            f"нода '{node_id}': не удалось записать канал '{channel_name}': {res.payload}"
        )
    log.info("нода '%s': канал '%s' записан в слот %d", node_id, channel_name, slot)
    return slot


async def send_channel(mc: MeshCore, channel_index: int | None, text: str, node_id: str) -> Any:
    if channel_index is None:
        raise RuntimeError(f"нода '{node_id}': канал не разрешён (resolve не выполнен) — нечего слать")
    log.debug("нода '%s': send_chan_msg слот=%s текст=%r", node_id, channel_index, text)
    return await mc.commands.send_chan_msg(channel_index, text)


def split_author(text: str) -> tuple[str, str]:
    """Разделить ``"Имя: текст"`` → ``(имя, текст)`` по ПЕРВОМУ ``": "``.

    Канальный кадр не несёт поля отправителя — MeshCore встраивает автора в тело
    как ``"Имя: текст"`` (так шлёт companion-приложение). Если ``": "`` нет или
    левая часть пуста — автор неизвестен: ``("", text)`` (текст не трогаем).
    """
    name, sep, body = text.partition(": ")
    if sep and name:
        return name, body
    return "", text


def channel_to_message(payload: dict[str, Any], endpoint: str, node_id: str) -> Message:
    ts = payload.get("sender_timestamp", 0)
    raw = payload.get("text", "")
    # автора несёт сам текст ("Имя: текст") — выносим в display_name, тело чистим
    name, text = split_author(raw)
    return Message(
        id=f"{endpoint}:{ts}:{hash(raw)}",  # id по сырому тексту — стабильный идентификатор кадра
        source=ChannelRef(node_id, endpoint),
        sender=Identity(display_name=name, transport_uid=LORA_SENDER_UID),
        text=text,
    )
