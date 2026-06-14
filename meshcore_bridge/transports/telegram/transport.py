"""Telegram-адаптер: порт ``Transport`` поверх ``aiogram``.

Особенности (см. §9/§10/§11): фильтрация по топику (forum thread), реакции-статусы
(``setMessageReaction``), персист ``getUpdates`` offset (A4), отключённый privacy mode.
"""
from __future__ import annotations

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


# Маппинг статус → эмодзи-реакция (§10). Уточняется при реализации.
STATUS_EMOJI: dict[DeliveryStatus, str] = {
    DeliveryStatus.PENDING: "🕐",
    DeliveryStatus.TRANSMITTING: "📤",
    DeliveryStatus.SENT: "✅",
    DeliveryStatus.FAILED: "⚠️",
    DeliveryStatus.UNKNOWN: "❓",
}
REJECT_EMOJI: dict[RejectReason, str] = {
    RejectReason.TOO_LONG: "📏",
    RejectReason.RATE_LIMIT: "🐢",
    RejectReason.TTL_EXPIRED: "⌛",
}


class TelegramTransport:
    capabilities = Capabilities(
        max_text_bytes=4096,
        egress_rate=RateSpec(20, 60, burst=20),
        supports_status_feedback=True,
        emits_tx_done=False,
    )

    def __init__(self, transport_id: str, tag: str, config: "TelegramConfig") -> None:
        self.id = transport_id
        self.tag = tag                      # тег источника в префиксе LoRa ("TG")
        self._config = config
        self._hub = Hub()

    async def start(self) -> None:
        # TODO(§9): aiogram Bot/Dispatcher; восстановить offset (A4); long-polling.
        raise NotImplementedError("TODO(§9): start polling")

    async def stop(self) -> None:
        raise NotImplementedError("TODO(§9): graceful stop + persist offset")

    async def send(self, target: ChannelRef, msg: Message) -> SendResult:
        # TODO(§9): отрисовать sender+text; учесть topic; экранировать MarkdownV2 (D4).
        raise NotImplementedError("TODO(§9): sendMessage в chat/topic")

    def subscribe(self) -> AsyncIterator[Message]:
        # TODO(§9): фильтр по топику; нормализация в Message; своё эхо по bot-id.
        return self._hub.subscribe()

    async def report_status(
        self,
        origin: ChannelRef,
        message_id: str,
        status: DeliveryStatus,
        reason: Optional[RejectReason] = None,
    ) -> None:
        # TODO(§10): setMessageReaction(STATUS_EMOJI/REJECT_EMOJI); идемпотентно (§11.1).
        raise NotImplementedError("TODO(§10): setMessageReaction")


class TelegramConfig:
    """Разобранный конфиг мессенджера (token, chat/topic-маршруты)."""
    # TODO(§12): token, список подписок (chat, topic).
