"""MeshCore-адаптер: порт ``Transport`` поверх библиотеки ``meshcore`` (§5.1).

Адаптер одного узла — тонкий оркестратор поверх одного радио (AD-6): жизненный
цикл, реконнект (M4), приём/отправка. Тип эндпоинта транспорту безразличен: он
держит набор ``EndpointHandler`` и делегирует им подготовку (``resolve``),
отправку (``send``) и разбор RX (``route_rx``). Вся type-specific логика — в
``mappers/`` (public/private/room_server + общий channel_util); единственный
``match`` по типу — в фабрике ``init_endpoint_handler``. Установка соединения — в
``connection``, классификация ответа устройства — в ``result``.

Commit зависит от типа эндпоинта (детали — в соответствующих хэндлерах):
  public / private  → send_chan_msg, commit = MSG_OK  (flood, без доставки)
  room_server       → send_login + send_msg_with_retry, commit = ACK 0x82 (+ backfill)

Точные вызовы `meshcore_py` (имена методов/событий) помечены ``# verify`` —
их нужно сверить на живом узле.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Optional, TYPE_CHECKING

import anyio
from meshcore import EventType as McEventType, MeshCore

from . import connection
from .mappers import (
    EndpointHandler,
    ResolveContext,
    collect_channel_names,
    init_endpoint_handler,
    route_rx,
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
        self._mc: MeshCore | None = None
        self._endpoints: dict[str, EndpointHandler] = {
            name: init_endpoint_handler(name, ep) for name, ep in node.endpoints.items()
        }
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
        # Кэш контактов фреймворка нужен для резолва имени автора room-server постов
        # (_resolve_author -> get_contact_by_key_prefix). auto_update держит его свежим
        # по contact-change событиям; ensure_contacts — первичная загрузка после connect.
        self._mc.auto_update_contacts = True
        await self._mc.ensure_contacts()  # verify: первичная загрузка кэша контактов на железе
        ctx = ResolveContext(
            mc=self._mc,
            node_id=self.id,
            channel_names=collect_channel_names(self._endpoints.values()),
            override_oldest_channel=self._override_oldest_channel,
            override_oldest_contact=self._override_oldest_contact,
        )
        for handler in self._endpoints.values():
            await handler.resolve(ctx)  # R3
        await self._mc.start_auto_message_fetching()  # verify; R2 / баг #1232
        # Либа типизирует колбэк как Future-возвращающий, но принимает и async (Coroutine) —
        # подавляем неточность типа на стыке.
        self._mc.subscribe(None, self.on_event)  # type: ignore[arg-type]
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
        """Сбросить текущее соединение перед реконнектом."""
        mc, self._mc = self._mc, None
        if mc is not None:
            try:
                await mc.disconnect()  # type: ignore[no-untyped-call]  # verify; у метода либы нет аннотаций
            except Exception:
                pass

    def _signal_disconnect(self) -> None:
        """Выставить event обрыва (вызывается из on_event или stop)."""
        ev = self._disconnect_ev
        if ev is not None and not ev.is_set():
            ev.set()

    # --- отправка -------------------------------------------------------------

    async def send(self, target: ChannelRef, msg: Message) -> SendResult:
        if self._mc is None:
            return SendResult.overloaded()  # реконнект в процессе — egress повторит (не FAILED)
        handler = self._endpoints.get(target.channel)
        if handler is None:
            return SendResult.failure(f"неизвестный эндпоинт {target.channel}")
        try:
            res = await handler.send(self._mc, msg.text, self.id)
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
        msg = route_rx(self._endpoints.values(), event, self.id, self._resolve_author)
        if msg is not None:
            await self._hub.publish(msg)

    def _resolve_author(self, pubkey_prefix: str) -> str | None:
        """Префикс ключа автора -> adv_name по кэшу контактов фреймворка (или None)."""
        mc = self._mc
        if mc is None:
            return None
        contact = mc.get_contact_by_key_prefix(pubkey_prefix)
        return contact.get("adv_name") if contact else None

    async def report_status(
        self,
        origin: ChannelRef,
        message_id: str,
        status: DeliveryStatus,
        reason: Optional[RejectReason] = None,
    ) -> None:
        return None  # LoRa не показывает статус
