"""MeshCore-адаптер: порт ``Transport`` поверх библиотеки ``meshcore`` (§5.1).

Вся MeshCore-специфика спрятана здесь: слот-индексы каналов, деривация PSK,
login в room server, ACK-фрейм, нормализация CHANNEL_MSG_RECV vs CONTACT_MSG_RECV.
Наружу торчит только generic-порт.

Три типа эндпоинтов (commit зависит от типа):
  public / private  → send_chan_msg, commit = MSG_OK  (flood, без доставки)
  room_server       → send_login + send_msg_with_retry, commit = ACK 0x82 (+ backfill)
"""
from __future__ import annotations

from enum import Enum
from typing import AsyncIterator, Optional

from ...domain.models import (
    Capabilities,
    ChannelRef,
    DeliveryStatus,
    Message,
    RateSpec,
    RejectReason,
    SendResult,
)
from ...core.hub import Hub


class EndpointType(Enum):
    """Тип LoRa-эндпоинта в терминах MeshCore-приложения (§5.1, конфиг)."""
    PUBLIC = "public"            # Public channel (общий PSK)
    PRIVATE = "private"          # Channel со своим secret
    ROOM_SERVER = "room_server"  # Room Server (direct + login + ACK)


class MeshCoreTransport:
    """Адаптер одного MeshCore-узла; обслуживает все его эндпоинты по одному радио."""

    # MeshCore: медленный коммит-источник; TX-done НЕТ (§5.1).
    capabilities = Capabilities(
        max_text_bytes=150,                   # реальный байтовый бюджет узла (~133 симв / 163 Б)
        egress_rate=RateSpec(6, 60),          # консервативно под duty cycle + airtime-pace
        supports_status_feedback=False,
        emits_tx_done=False,
    )

    def __init__(self, transport_id: str, config: "MeshCoreConfig") -> None:
        self.id = transport_id
        self._config = config
        self._hub = Hub()
        self._mc = None  # будущий объект lib `meshcore`

    async def start(self) -> None:
        """connect → set-time → resolve эндпоинтов → auto-fetch (§5.1 lifecycle).

        TODO(§5.1):
          - connect по connection (usb VID:PID / serial / tcp / ble)
          - set-time (R6)
          - public/private: resolve name→slot-index (или провизион канала с PSK) (R3)
          - room_server: найти контакт по pubkey (advert) + send_login(pubkey, pwd) (R3)
          - start_auto_message_fetching() + страховочный get_msg-дренаж (R2/#1232)
        """
        raise NotImplementedError("TODO(§5.1): lifecycle start()")

    async def stop(self) -> None:
        raise NotImplementedError("TODO(§5.1): graceful disconnect")

    async def send(self, target: ChannelRef, msg: Message) -> SendResult:
        """Отправка по типу эндпоинта; commit = MSG_OK (канал) / ACK (room_server).

        TODO(§5.1):
          - public/private → send_chan_msg(idx, msg.text); commit=MSG_OK; TABLE_FULL→busy (R4)
          - room_server    → send_msg_with_retry(pubkey, msg.text); ждём ACK 0x82 в таймаут
        """
        raise NotImplementedError("TODO(§5.1): send() по типу эндпоинта")

    def subscribe(self) -> AsyncIterator[Message]:
        """Горячий RX-поток: нормализованные InboundMessage из RX-событий узла.

        TODO(§5.1): public/private ← CHANNEL_MSG_RECV нужного индекса;
        room_server ← CONTACT_MSG_RECV от pubkey; фильтр ADVERTISEMENT/ACK/чужих;
        извлечь автора для префикса; публиковать в self._hub.
        """
        return self._hub.subscribe()

    async def report_status(
        self,
        origin: ChannelRef,
        message_id: str,
        status: DeliveryStatus,
        reason: Optional[RejectReason] = None,
    ) -> None:
        # No-op: LoRa не умеет показывать статус (supports_status_feedback=False).
        return None


class MeshCoreConfig:
    """Разобранный конфиг узла (connection + endpoints). Заполняется из config.schema."""
    # TODO(§12): connection, dict[name -> (EndpointType, secret/pubkey/password)],
    # commit_timeout_seconds.
