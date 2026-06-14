"""Порт ``Transport`` (§5).

И LoRa-клиент, и мессенджер реализуют один протокол. Дуплекс включает обратный
канал статусов (``report_status``) — для отрисовки реакции в мессенджере.
"""
from __future__ import annotations

from typing import AsyncIterator, Optional, Protocol, runtime_checkable

from .models import (
    Capabilities,
    ChannelRef,
    DeliveryStatus,
    Message,
    RejectReason,
    SendResult,
)


@runtime_checkable
class Transport(Protocol):
    id: str
    capabilities: Capabilities

    async def start(self) -> None: ...
    async def stop(self) -> None: ...

    # Исходящая сторона: одна модель Message на оба направления.
    # LoRa-адаптер транслирует msg.text как есть (ядро уже собрало префикс);
    # мессенджер-адаптер сам форматирует sender + text.
    # Для LoRa send() РЕЗОЛВИТСЯ по commit узла, а не по записи в линк (AD-5/§5.1).
    async def send(self, target: ChannelRef, msg: Message) -> SendResult: ...

    # Горячий мультикаст-поток входящих (§8).
    def subscribe(self) -> AsyncIterator[Message]: ...

    # Обратный канал статусов. No-op, если supports_status_feedback=False.
    # reason заполняется только для REJECTED (см. RejectReason).
    async def report_status(
        self,
        origin: ChannelRef,
        message_id: str,
        status: DeliveryStatus,
        reason: Optional[RejectReason] = None,
    ) -> None: ...
