"""MeshCore-адаптер: порт ``Transport`` поверх библиотеки ``meshcore`` (§5.1).

Вся MeshCore-специфика спрятана здесь: слот-индексы каналов, деривация PSK,
login в room server, ACK-фрейм, нормализация CHANNEL_MSG_RECV vs CONTACT_MSG_RECV.

Commit зависит от типа эндпоинта:
  public / private  → send_chan_msg, commit = MSG_OK  (flood, без доставки)
  room_server       → send_login + send_msg_with_retry, commit = ACK 0x82 (+ backfill)

Точные вызовы `meshcore_py` (имена методов/событий) помечены ``# verify`` —
их нужно сверить на живом узле.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator, Optional, TYPE_CHECKING, assert_never

import anyio

from meshcore import MeshCore, EventType as McEventType
from serial.tools import list_ports

from ..hub import Hub
from ...domain.ports import Transport
from ...config.schema import (
    BleConnection,
    SerialConnection,
    TcpConnection,
    UsbConnection,
    PublicEndpoint,
    PrivateEndpoint,
    RoomServerEndpoint,
)
from ...domain.models import (
    Capabilities,
    ChannelRef,
    DeliveryStatus,
    Identity,
    LORA_SENDER_UID,
    Message,
    RateSpec,
    RejectReason,
    SendResult,
)

if TYPE_CHECKING:
    from ...config.schema import MeshCoreNode

EV_CHANNEL_MSG = McEventType.CHANNEL_MSG_RECV
EV_CONTACT_MSG = McEventType.CONTACT_MSG_RECV
EV_DISCONNECTED = McEventType.DISCONNECTED

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


@dataclass
class RoomServerEndpointState:
    name: str
    pubkey: str
    password: str | None


EndpointState = PublicEndpointState | PrivateEndpointState | RoomServerEndpointState


def init_endpoint_state(
    name: str, ep: PublicEndpoint | PrivateEndpoint | RoomServerEndpoint
) -> EndpointState:
    match ep:
        case PublicEndpoint():
            return PublicEndpointState(name=name, channel_name=ep.channel_name)
        case PrivateEndpoint():
            return PrivateEndpointState(name=name, channel_name=ep.channel_name, secret=ep.secret)
        case RoomServerEndpoint():
            return RoomServerEndpointState(name=name, pubkey=ep.pubkey, password=ep.password)
        case _ as unreachable:
            assert_never(unreachable)


class MeshCoreTransport(Transport):
    """Адаптер одного MeshCore-узла; обслуживает все его эндпоинты по одному радио."""

    capabilities = Capabilities(
        max_text_bytes=150,  # ~133 симв / MAX_CHANNEL_DATA=163 Б
        egress_rate=RateSpec(6, 60),
        supports_status_feedback=False,
        emits_tx_done=False,
    )

    def __init__(self, node: MeshCoreNode) -> None:
        self.id = node.id
        self.connection = node.connection
        self._hub = Hub()
        self._mc = None  # объект meshcore.MeshCore
        self._endpoints: dict[str, EndpointState] = {
            name: init_endpoint_state(name, ep) for name, ep in node.endpoints.items()
        }
        self._by_index: dict[int, str] = {}  # channel_index → endpoint name (RX-маршрут)
        self._by_pubkey: dict[str, str] = {}  # pubkey prefix → endpoint name (RX room_server)
        self._stopping: bool = False
        self._disconnect_ev: anyio.Event | None = None

    # --- жизненный цикл -------------------------------------------------------

    async def start(self) -> None:
        # Event создаётся до connect(), чтобы DISCONNECTED не пропустить при немедленном обрыве
        self._disconnect_ev = anyio.Event()
        self._mc = await self.connect()
        await self.set_time()  # R6
        for ep in self._endpoints.values():
            await self.resolve_endpoint(ep)  # R3
        await self._mc.start_auto_message_fetching()  # verify; R2 / баг #1232
        self._mc.subscribe(None, self.on_event)
        log.info("нода '%s' запущена: %d эндпоинтов активно", self.id, len(self._endpoints))

    async def stop(self) -> None:
        self._stopping = True
        self._signal_disconnect()  # разбудить run(), чтобы он вышел из ожидания
        await self._teardown()

    async def run(self) -> None:
        """Монитор переподключения (M4): ждёт DISCONNECTED и перезапускает start()."""
        delay = 1.0
        while not self._stopping:
            # ждём сигнала обрыва; None — start() ещё не вызывался или упал
            ev = self._disconnect_ev
            if ev is not None:
                await ev.wait()
            if self._stopping:
                break
            log.info("нода '%s': обрыв соединения, повтор через %.0f с", self.id, delay)
            await anyio.sleep(delay)
            await self._teardown()
            try:
                await self.start()
                delay = 1.0  # сброс backoff после успешного реконнекта
                log.info("нода '%s': переподключена", self.id)
            except anyio.get_cancelled_exc_class():
                raise
            except Exception as exc:
                log.warning("нода '%s': реконнект не удался (%s), следующая попытка через %.0f с", self.id, exc, delay)
                delay = min(delay * 2, 60.0)
                # start() уже выставил свежий _disconnect_ev — сбрасываем,
                # чтобы следующая итерация не зависла на нём
                self._disconnect_ev = None

    async def _teardown(self) -> None:
        """Сбросить текущее соединение и routing-таблицы перед реконнектом."""
        mc, self._mc = self._mc, None
        self._by_index.clear()
        self._by_pubkey.clear()
        if mc is not None:
            try:
                await mc.disconnect()  # verify
            except Exception:
                pass

    def _signal_disconnect(self) -> None:
        """Выставить event обрыва (вызывается из on_event или stop)."""
        ev = self._disconnect_ev
        if ev is not None and not ev.is_set():
            ev.set()

    async def _connect_mc(self, coro, label: str):
        try:
            mc = await coro
        except Exception as exc:
            raise RuntimeError(f"нода '{self.id}': не удалось подключиться {label}: {exc}") from exc
        if mc is None:
            raise RuntimeError(f"нода '{self.id}': {label} — нет ответа от устройства")
        log.info("нода '%s' подключена: %s", self.id, label)
        return mc

    async def connect(self):
        match self.connection:
            case TcpConnection(host=host, port=port):
                return await self._connect_mc(
                    MeshCore.create_tcp(host, port), f"TCP {host}:{port}"  # verify
                )
            case SerialConnection(port=port):
                return await self._connect_mc(
                    MeshCore.create_serial(port), f"serial {port}"  # verify
                )
            case UsbConnection(device_id=device_id):
                serial_port = self.port_by_vidpid(device_id)
                return await self._connect_mc(
                    MeshCore.create_serial(serial_port), f"USB {device_id} ({serial_port})"
                )
            case BleConnection(address=address):
                return await self._connect_mc(
                    MeshCore.create_ble(address), f"BLE {address}"
                )
            case _ as unreachable:
                assert_never(unreachable)

    @staticmethod
    def port_by_vidpid(device_id: str) -> str:
        """Найти serial-порт по VID:PID (usb-соединение)."""
        vid, pid = (int(x, 16) for x in device_id.split(":"))
        for p in list_ports.comports():
            if p.vid == vid and p.pid == pid:
                return p.device
        raise RuntimeError(f"USB-устройство {device_id} не найдено")

    async def set_time(self) -> None:
        await self._mc.commands.set_time(int(time.time()))  # verify

    async def resolve_endpoint(self, ep: EndpointState) -> None:
        match ep:
            case PublicEndpointState() | PrivateEndpointState():
                ep.channel_index = await self.resolve_channel_index(ep)
                self._by_index[ep.channel_index] = ep.name
            case RoomServerEndpointState():
                # send_login принимает 6-байтовый префикс pubkey (12 hex-символов)  # verify
                await self._mc.commands.send_login(ep.pubkey[:12], ep.password or "")
                self._by_pubkey[ep.pubkey[:12]] = ep.name
            case _ as unreachable:
                assert_never(unreachable)

    async def resolve_channel_index(self, ep: PublicEndpointState | PrivateEndpointState) -> int:
        """Найти канал на устройстве по имени, вернуть его слот-индекс."""
        device_info = await self._mc.commands.send_device_query()
        if device_info.is_error():
            raise RuntimeError(f"нода '{self.id}': не удалось получить device info")
        max_channels = device_info.payload.get("max_channels", 8)

        found: list[str] = []
        for idx in range(max_channels):
            ch = await self._mc.commands.get_channel(idx)
            if ch.is_error():
                break
            name = ch.payload.get("channel_name", "")
            found.append(name)
            if name == ep.channel_name:
                log.debug("нода '%s': канал '%s' → слот %d", self.id, ep.channel_name, idx)
                return idx

        non_empty = [n for n in found if n]
        log.debug("нода '%s': каналы на устройстве: %s", self.id, non_empty)
        raise RuntimeError(
            f"нода '{self.id}': канал '{ep.channel_name}' не найден на устройстве. "
            f"Доступные каналы: {non_empty}. "
            f"Исправьте channel_name в config.yaml."
        )

    # --- отправка -------------------------------------------------------------

    async def send(self, target: ChannelRef, msg: Message) -> SendResult:
        if self._mc is None:
            return SendResult.overloaded()  # реконнект в процессе — egress повторит (не FAILED)
        ep = self._endpoints.get(target.channel)
        if ep is None:
            return SendResult.failure(f"неизвестный эндпоинт {target.channel}")
        try:
            match ep:
                case PublicEndpointState() | PrivateEndpointState():
                    res = await self._mc.commands.send_chan_msg(ep.channel_index, msg.text)
                case RoomServerEndpointState():
                    res = await self._mc.commands.send_msg_with_retry(
                        ep.pubkey[:12], msg.text
                    )  # verify
                case _ as unreachable:
                    assert_never(unreachable)
            return self.classify(res)
        except Exception as exc:  # noqa: BLE001
            log.exception("MeshCore send в %s упал", target.channel)
            return SendResult.failure(str(exc))

    @staticmethod
    def classify(res) -> SendResult:
        if res.is_error():
            payload = res.payload if isinstance(res.payload, dict) else {}
            if payload.get("error_code") == 3:  # ERR_CODE_TABLE_FULL
                return SendResult.overloaded()
            detail = payload.get("code_string") or payload.get("reason") or str(res.payload)
            return SendResult.failure(detail)
        return SendResult.success()

    # --- приём ----------------------------------------------------------------

    def subscribe(self) -> AsyncIterator[Message]:
        return self._hub.subscribe()

    async def on_event(self, event) -> None:
        """Нормализовать RX-событие meshcore_py в доменный Message и опубликовать."""
        if event.type == EV_DISCONNECTED:
            self._signal_disconnect()
            return
        msg = self.normalize(event)
        if msg is not None:
            await self._hub.publish(msg)

    def normalize(self, event) -> Optional[Message]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == EV_CHANNEL_MSG:
            endpoint = self._by_index.get(payload.get("channel_idx", -1))
            if endpoint is None:
                return None
            ts = payload.get("sender_timestamp", 0)
            text = payload.get("text", "")
            # CHANNEL_MSG_RECV не несёт имени отправителя — callsign уже в тексте
            return Message(
                id=f"{endpoint}:{ts}:{hash(text)}",
                source=ChannelRef(self.id, endpoint),
                sender=Identity(display_name="", transport_uid=LORA_SENDER_UID),
                text=text,
            )
        if event.type == EV_CONTACT_MSG:
            pubkey = payload.get("pubkey_prefix", "")
            endpoint = self._by_pubkey.get(pubkey)  # verify: prefix vs full pubkey
            if endpoint is None:
                return None
            text = payload.get("text", "")
            return Message(
                id=f"{endpoint}:{payload.get('sender_timestamp', 0)}",
                source=ChannelRef(self.id, endpoint),
                sender=Identity(display_name=pubkey, transport_uid=LORA_SENDER_UID),
                text=text,
            )
        return None  # ADVERTISEMENT/ACK/чужое — фильтр

    async def report_status(
        self,
        origin: ChannelRef,
        message_id: str,
        status: DeliveryStatus,
        reason: Optional[RejectReason] = None,
    ) -> None:
        return None  # LoRa не показывает статус
