"""MeshCore-адаптер: порт ``Transport`` поверх библиотеки ``meshcore`` (§5.1).

Адаптер одного узла — тонкий оркестратор поверх одного радио (AD-6): жизненный
цикл, реконнект (M4), маршрутизация RX и ``match``-диспетчеризация по типу
эндпоинта. Вся type-specific логика вынесена в ``mappers/`` (channel, room_server),
установка соединения — в ``connection``, классификация ответа — в ``result``.

Commit зависит от типа эндпоинта (детали — в соответствующих мапперах):
  public / private  → send_chan_msg, commit = MSG_OK  (flood, без доставки)
  room_server       → send_login + send_msg_with_retry, commit = ACK 0x82 (+ backfill)

Точные вызовы `meshcore_py` (имена методов/событий) помечены ``# verify`` —
их нужно сверить на живом узле.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Optional, TYPE_CHECKING, assert_never

import anyio

from meshcore import EventType as McEventType

from . import connection
from .mappers import (
    EndpointState,
    PrivateEndpointState,
    PublicEndpointState,
    RoomServerEndpointState,
    channel,
    init_endpoint_state,
    room_server,
)
from .result import classify
from ..hub import Hub
from ...domain.ports import Transport
from ...domain.models import (
    Capabilities,
    ChannelRef,
    DeliveryStatus,
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
        self._log_raw_rx = node.log_raw_rx
        self._override_oldest_contact = node.policies.override_oldest_contact_on_full
        self._override_oldest_channel = node.policies.override_oldest_channel_on_full
        self._hub = Hub()
        self._mc: Any = None  # объект meshcore.MeshCore
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
        try:
            self._mc = await connection.connect(self.connection, self.id)
        except RuntimeError as exc:
            # Устройство не ответило на handshake — сигналим disconnect,
            # чтобы run() подхватил и повторил попытку с backoff.
            log.warning("нода '%s': %s — ожидаю reconnect", self.id, exc)
            self._signal_disconnect()
            return
        await connection.set_time(self._mc)  # R6
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
            except anyio.get_cancelled_exc_class():
                raise
            except Exception as exc:
                log.warning("нода '%s': реконнект не удался (%s), следующая попытка через %.0f с", self.id, exc, delay)
                delay = min(delay * 2, 60.0)
                # start() уже выставил свежий _disconnect_ev — сбрасываем,
                # чтобы следующая итерация не зависла на нём
                self._disconnect_ev = None
                continue
            if self._mc is not None:
                delay = 1.0  # сброс backoff после успешного реконнекта
                log.info("нода '%s': переподключена", self.id)
            else:
                # start() поймал ошибку connect() — устройство не отвечает,
                # применяем backoff чтобы не спамить каждую секунду
                delay = min(delay * 2, 60.0)

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

    # --- резолв эндпоинтов ----------------------------------------------------

    async def resolve_endpoint(self, ep: EndpointState) -> None:
        match ep:
            case PublicEndpointState() | PrivateEndpointState():
                ep.channel_index = await channel.resolve_channel(
                    self._mc, ep, self.id,
                    configured_channel_names=self._channel_names(),
                    override_oldest=self._override_oldest_channel,
                )
                self._by_index[ep.channel_index] = ep.name
            case RoomServerEndpointState():
                await room_server.resolve_room_server(
                    self._mc, ep, self.id,
                    override_oldest_contact=self._override_oldest_contact,
                )
                self._by_pubkey[ep.pubkey[:12]] = ep.name  # 6-байтовый prefix для RX-маршрутизации
            case _ as unreachable:
                assert_never(unreachable)

    def _channel_names(self) -> set[str]:
        """Имена всех channel-эндпоинтов из конфига (для вытеснения чужих слотов)."""
        return {
            e.channel_name
            for e in self._endpoints.values()
            if isinstance(e, (PublicEndpointState, PrivateEndpointState))
        }

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
                    res = await channel.send_channel(self._mc, ep, msg.text, self.id)
                case RoomServerEndpointState():
                    res = await room_server.send_room_server(self._mc, ep, msg.text)
                case _ as unreachable:
                    assert_never(unreachable)
            result = classify(res)
            if not result.ok:
                if result.busy:
                    log.debug(
                        "нода '%s': send в %s временная ошибка: %s — повтор",
                        self.id, target.channel, result.detail,
                    )
                else:
                    log.warning(
                        "нода '%s': send в %s вернул ошибку: %s (payload=%s)",
                        self.id, target.channel, result.detail, res.payload,
                    )
            return result
        except Exception as exc:  # noqa: BLE001
            log.exception("MeshCore send в %s упал", target.channel)
            return SendResult.failure(str(exc))

    # --- приём ----------------------------------------------------------------

    def subscribe(self) -> AsyncIterator[Message]:
        return self._hub.subscribe()

    async def on_event(self, event: Any) -> None:
        """Нормализовать RX-событие meshcore_py в доменный Message и опубликовать."""
        if event.type == McEventType.RX_LOG_DATA:
            if self._log_raw_rx:
                log.debug("нода '%s': событие %s payload=%s", self.id, event.type, event.payload)
        else:
            log.debug("нода '%s': событие %s payload=%s", self.id, event.type, event.payload)
        if event.type == EV_DISCONNECTED:
            self._signal_disconnect()
            return
        msg = self.normalize(event)
        if msg is not None:
            await self._hub.publish(msg)

    def normalize(self, event: Any) -> Optional[Message]:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if event.type == EV_CHANNEL_MSG:
            idx = payload.get("channel_idx", -1)
            endpoint = self._by_index.get(idx)
            if endpoint is None:
                log.warning(
                    "нода '%s': CHANNEL_MSG_RECV с channel_idx=%s не совпадает с known=%s — дроп",
                    self.id, idx, list(self._by_index.keys()),
                )
                return None
            return channel.channel_to_message(payload, endpoint, self.id)
        if event.type == EV_CONTACT_MSG:
            pubkey = payload.get("pubkey_prefix", "")
            endpoint = self._by_pubkey.get(pubkey)  # verify: prefix vs full pubkey
            if endpoint is None:
                return None
            return room_server.room_server_to_message(payload, endpoint, self.id)
        return None  # ADVERTISEMENT/ACK/чужое — фильтр

    async def report_status(
        self,
        origin: ChannelRef,
        message_id: str,
        status: DeliveryStatus,
        reason: Optional[RejectReason] = None,
    ) -> None:
        return None  # LoRa не показывает статус
