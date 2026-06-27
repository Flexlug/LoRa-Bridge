"""Room server MeshCore: одна точка входа — ``RoomServerHandler``.

Commit = ACK 0x82 (+ backfill), доставка реальная. Специфика типа: контакт типа
ROOM в таблице устройства (с вытеснением старейшего при TABLE_FULL), login по
pubkey+password, отправка через send_msg_with_retry. RX-событие — CONTACT_MSG_RECV
(маршрутизация по 6-байтовому prefix pubkey). Точные вызовы ``meshcore_py``
помечены ``# verify``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, ClassVar

from meshcore import EventType as McEventType

from .handler import EV_CONTACT_MSG, EndpointHandler, ResolveContext
from ....domain.models import (
    ChannelRef,
    Identity,
    LORA_SENDER_UID,
    Message,
)

log = logging.getLogger(__name__)


@dataclass
class RoomServerHandler(EndpointHandler):
    name: str
    pubkey: str
    password: str | None = None
    rx_event_type: ClassVar = EV_CONTACT_MSG

    async def resolve(self, ctx: ResolveContext) -> None:
        await resolve_room_server(
            ctx.mc,
            name=self.name,
            pubkey=self.pubkey,
            password=self.password,
            node_id=ctx.node_id,
            override_oldest_contact=ctx.override_oldest_contact,
        )

    async def send(self, mc: Any, text: str, node_id: str) -> Any:
        return await send_room_server(mc, self.pubkey, text)

    def try_rx(self, payload: dict[str, Any], node_id: str) -> Message | None:
        # 6-байтовый prefix pubkey идентифицирует room server в CONTACT_MSG_RECV
        if payload.get("pubkey_prefix", "") != self.pubkey[:12]:  # verify: prefix vs full pubkey
            return None
        return room_server_to_message(payload, self.name, node_id)

    def rx_key(self) -> str:
        return f"pubkey_prefix={self.pubkey[:12]}"


async def resolve_room_server(
    mc: Any, *, name: str, pubkey: str, password: str | None, node_id: str,
    override_oldest_contact: bool,
) -> None:
    """Подготовить room server к работе: контакт в таблице + login."""
    await ensure_contact(
        mc, name=name, pubkey=pubkey, node_id=node_id,
        override_oldest_contact=override_oldest_contact,
    )
    await login_room_server(mc, name=name, pubkey=pubkey, password=password, node_id=node_id)


async def resolve_or_override(
    try_add: Callable[[], Awaitable[bool]],
    evict_and_retry: Callable[[], Awaitable[bool]],
    flag: bool,
) -> bool:
    """Добавить ресурс; при TABLE_FULL и включённом флаге — вытеснить старый и повторить."""
    if await try_add():
        return True
    if flag:
        return await evict_and_retry()
    return False


async def try_add_contact(mc: Any, contact: dict[str, Any], node_id: str) -> bool:
    """True = добавлен, False = TABLE_FULL или другая ошибка."""
    res = await mc.commands.add_contact(contact)
    if not res.is_error():
        return True
    if res.payload.get("error_code") != 3:  # не TABLE_FULL
        log.warning("нода '%s': ошибка добавления контакта: %s", node_id, res.payload)
    return False


async def evict_oldest_contact_and_add(mc: Any, contact: dict[str, Any], node_id: str) -> bool:
    """Удалить самый старый контакт (по last_advert) и добавить новый."""
    log.info("нода '%s': таблица контактов полна — удаляю самый старый", node_id)
    contacts_ev = await mc.commands.get_contacts()
    if contacts_ev.is_error():
        log.warning("нода '%s': не удалось получить список контактов: %s", node_id, contacts_ev.payload)
        return False
    contacts: dict[str, dict[str, Any]] = contacts_ev.payload
    if not contacts:
        log.warning("нода '%s': список контактов пуст, но таблица переполнена", node_id)
        return False
    oldest = min(contacts.values(), key=lambda c: c.get("last_advert", 0))
    log.info(
        "нода '%s': удаляю контакт '%s' (last_advert=%s)",
        node_id, oldest.get("adv_name", "?"), oldest.get("last_advert"),
    )
    rm = await mc.commands.remove_contact(oldest["public_key"])
    if rm.is_error():
        log.warning("нода '%s': не удалось удалить контакт: %s", node_id, rm.payload)
        return False
    return await try_add_contact(mc, contact, node_id)


async def ensure_contact(
    mc: Any, *, name: str, pubkey: str, node_id: str, override_oldest_contact: bool
) -> None:
    pubkey_bytes = bytes.fromhex(pubkey)[:32]
    check = await mc.commands.get_contact_by_key(pubkey_bytes)
    if not check.is_error():
        return
    log.info("нода '%s': контакт '%s' не найден — добавляю", node_id, name)
    contact = {
        "public_key": pubkey,
        "type": 3,  # ROOM
        "flags": 0,
        "out_path": "",
        "out_path_len": -1,  # flood
        "out_path_hash_mode": 0,
        "adv_name": name,
        "last_advert": 0,
        "adv_lat": 0.0,
        "adv_lon": 0.0,
    }
    ok = await resolve_or_override(
        try_add=lambda: try_add_contact(mc, contact, node_id),
        evict_and_retry=lambda: evict_oldest_contact_and_add(mc, contact, node_id),
        flag=override_oldest_contact,
    )
    if ok:
        log.info("нода '%s': контакт '%s' добавлен", node_id, name)
    else:
        log.warning("нода '%s': не удалось добавить контакт '%s'", node_id, name)


async def login_room_server(
    mc: Any, *, name: str, pubkey: str, password: str | None, node_id: str
) -> None:
    login_failed = False

    def on_login_failed(_event: object) -> None:
        nonlocal login_failed
        login_failed = True

    sub = mc.commands.dispatcher.subscribe(McEventType.LOGIN_FAILED, on_login_failed)
    try:
        sent = await mc.commands._send_login_raw(pubkey, password or "")  # noqa: SLF001
        if sent is None or sent.type == McEventType.ERROR:
            log.warning(
                "нода '%s': логин в room server '%s' — устройство вернуло ошибку: %s",
                node_id, name, sent.payload if sent else "нет ответа",
            )
            return
        suggested_s = sent.payload.get("suggested_timeout", 0) / 800
        wait_s = max(suggested_s, 15.0)
        log.debug(
            "нода '%s': логин в '%s' отправлен, suggested=%.1f с, ждём LOGIN_SUCCESS %.1f с",
            node_id, name, suggested_s, wait_s,
        )
        login_event = await mc.commands.dispatcher.wait_for_event(
            McEventType.LOGIN_SUCCESS, timeout=wait_s
        )
        if login_event is not None:
            log.info("нода '%s': логин в room server '%s' успешен", node_id, name)
        elif login_failed:
            log.warning("нода '%s': логин в room server '%s' отклонён (неверный пароль?)", node_id, name)
        else:
            log.warning("нода '%s': логин в room server '%s' — нет ответа (вне зоны?)", node_id, name)
    finally:
        sub.unsubscribe()


async def send_room_server(mc: Any, pubkey: str, text: str) -> Any:
    return await mc.commands.send_msg_with_retry(pubkey, text)  # verify


def room_server_to_message(payload: dict[str, Any], endpoint: str, node_id: str) -> Message:
    pubkey = payload.get("pubkey_prefix", "")
    text = payload.get("text", "")
    return Message(
        id=f"{endpoint}:{payload.get('sender_timestamp', 0)}",
        source=ChannelRef(node_id, endpoint),
        sender=Identity(display_name=pubkey, transport_uid=LORA_SENDER_UID),
        text=text,
    )
