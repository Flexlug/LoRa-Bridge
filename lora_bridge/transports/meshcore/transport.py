"""MeshCore-адаптер: порт ``Transport`` поверх библиотеки ``meshcore`` (§5.1).

Вся MeshCore-специфика спрятана здесь: слот-индексы каналов, деривация PSK,
login в room server, ACK-фрейм, нормализация CHANNEL_MSG_RECV vs CONTACT_MSG_RECV.

Commit зависит от типа эндпоинта:
  public / private  → send_chan_msg, commit = MSG_OK  (flood, без доставки)
  room_server       → send_login + send_msg_with_retry, commit = ACK 0x82 (+ backfill)

ВНИМАНИЕ: точные вызовы `meshcore_py` (имена методов/событий) помечены
``# verify`` — их нужно сверить на живом узле; импорт библиотеки ленивый, чтобы
пакет и ядро импортировались без неё.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, AsyncIterator, Optional

from ...domain.models import (
    Capabilities,
    ChannelRef,
    DeliveryStatus,
    Identity,
    Message,
    RateSpec,
    RejectReason,
    SendResult,
)
from ..hub import Hub

if TYPE_CHECKING:
    from ...config.schema import MeshCoreNode

log = logging.getLogger(__name__)


class EndpointType(Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    ROOM_SERVER = "room_server"


@dataclass
class _Endpoint:
    name: str
    type: EndpointType
    secret: Optional[str] = None
    pubkey: Optional[str] = None
    password: Optional[str] = None
    channel_index: Optional[int] = None   # резолвится для public/private при start()


class MeshCoreTransport:
    """Адаптер одного MeshCore-узла; обслуживает все его эндпоинты по одному радио."""

    capabilities = Capabilities(
        max_text_bytes=150,                   # ~133 симв / MAX_CHANNEL_DATA=163 Б
        egress_rate=RateSpec(6, 60),
        supports_status_feedback=False,
        emits_tx_done=False,
    )

    def __init__(self, node: "MeshCoreNode") -> None:
        self.id = node.id
        self._connection = node.connection
        self._hub = Hub()
        self._mc = None                       # объект meshcore.MeshCore
        self._endpoints: dict[str, _Endpoint] = {
            name: _Endpoint(
                name=name,
                type=EndpointType(ep.type),
                secret=getattr(ep, "secret", None),
                pubkey=getattr(ep, "pubkey", None),
                password=getattr(ep, "password", None),
            )
            for name, ep in node.endpoints.items()
        }
        self._by_index: dict[int, str] = {}   # channel_index → endpoint name (RX-маршрут)
        self._by_pubkey: dict[str, str] = {}  # pubkey → endpoint name (RX room_server)

    # --- жизненный цикл -------------------------------------------------------

    async def start(self) -> None:
        self._mc = await self._connect()                      # verify: фабрики meshcore_py
        await self._set_time()                                # R6
        for ep in self._endpoints.values():
            await self._resolve_endpoint(ep)                  # R3
        await self._mc.start_auto_message_fetching()          # verify; R2 / баг #1232
        self._mc.subscribe(self._on_event)                    # verify: подписка на RX-события

    async def stop(self) -> None:
        if self._mc is not None:
            await self._mc.disconnect()                       # verify
            self._mc = None

    async def _connect(self):
        from meshcore import MeshCore                         # lazy import

        c = self._connection
        if c.type == "tcp":
            return await MeshCore.create_tcp(c.host, c.port)  # verify
        if c.type == "serial":
            return await MeshCore.create_serial(c.port)       # verify
        if c.type == "usb":
            return await MeshCore.create_serial(self._port_by_vidpid(c.device_id))
        if c.type == "ble":
            return await MeshCore.create_ble(c.address)       # verify
        raise ValueError(f"неизвестный тип соединения: {c.type}")

    @staticmethod
    def _port_by_vidpid(device_id: str) -> str:
        """Найти serial-порт по VID:PID (usb-соединение)."""
        from serial.tools import list_ports

        vid, pid = (int(x, 16) for x in device_id.split(":"))
        for p in list_ports.comports():
            if p.vid == vid and p.pid == pid:
                return p.device
        raise RuntimeError(f"USB-устройство {device_id} не найдено")

    async def _set_time(self) -> None:
        import time
        await self._mc.set_time(int(time.time()))             # verify

    async def _resolve_endpoint(self, ep: _Endpoint) -> None:
        if ep.type in (EndpointType.PUBLIC, EndpointType.PRIVATE):
            ep.channel_index = await self._resolve_channel_index(ep)
            self._by_index[ep.channel_index] = ep.name
        else:  # ROOM_SERVER
            await self._mc.send_login(ep.pubkey, ep.password or "")   # verify
            self._by_pubkey[ep.pubkey] = ep.name

    async def _resolve_channel_index(self, ep: _Endpoint) -> int:
        # verify: перечислить каналы узла, найти по имени/PSK; нет — провизионить.
        # public = индекс 0; для private нужен add-channel с secret.
        if ep.type == EndpointType.PUBLIC:
            return 0
        raise NotImplementedError(
            f"resolve channel index для private '{ep.name}' (provision PSK) — verify meshcore_py"
        )

    # --- отправка -------------------------------------------------------------

    async def send(self, target: ChannelRef, msg: Message) -> SendResult:
        ep = self._endpoints.get(target.channel)
        if ep is None:
            return SendResult.failure(f"неизвестный эндпоинт {target.channel}")
        try:
            if ep.type in (EndpointType.PUBLIC, EndpointType.PRIVATE):
                res = await self._mc.send_chan_msg(ep.channel_index, msg.text)   # verify; commit=MSG_OK
            else:
                res = await self._mc.send_msg_with_retry(ep.pubkey, msg.text)    # verify; commit=ACK
            return self._classify(res)
        except Exception as exc:                              # noqa: BLE001
            log.exception("MeshCore send в %s упал", target.channel)
            return SendResult.failure(str(exc))

    @staticmethod
    def _classify(res) -> SendResult:
        # verify: маппинг ответа meshcore_py. TABLE_FULL → busy (R4), ok → success.
        if getattr(res, "table_full", False):
            return SendResult.overloaded()
        if getattr(res, "ok", True):
            return SendResult.success()
        return SendResult.failure(str(res))

    # --- приём ----------------------------------------------------------------

    def subscribe(self) -> AsyncIterator[Message]:
        return self._hub.subscribe()

    async def _on_event(self, event) -> None:
        """Нормализовать RX-событие meshcore_py в доменный Message и опубликовать."""
        msg = self._normalize(event)
        if msg is not None:
            await self._hub.publish(msg)

    def _normalize(self, event) -> Optional[Message]:
        # verify: реальные поля события meshcore_py (тип, channel_index, pubkey, text, sender).
        etype = getattr(event, "type", None)
        if etype == "CHANNEL_MSG_RECV":
            endpoint = self._by_index.get(getattr(event, "channel", -1))
        elif etype == "CONTACT_MSG_RECV":
            endpoint = self._by_pubkey.get(getattr(event, "pubkey", ""))
        else:
            return None                                       # ADVERTISEMENT/ACK/чужое — фильтр
        if endpoint is None:
            return None
        author = getattr(event, "sender_name", None) or "unknown"
        return Message(
            id=str(getattr(event, "id", "") or f"{endpoint}:{hash(getattr(event, 'text', ''))}"),
            source=ChannelRef(self.id, endpoint),
            sender=Identity(display_name=author, transport_uid=author),
            text=getattr(event, "text", ""),
            origin_tag=getattr(event, "packet_hash", None),
        )

    async def report_status(
        self, origin: ChannelRef, message_id: str,
        status: DeliveryStatus, reason: Optional[RejectReason] = None,
    ) -> None:
        return None                                           # LoRa не показывает статус
