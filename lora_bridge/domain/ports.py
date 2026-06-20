"""Порт ``Transport`` (§5).

И LoRa-клиент, и мессенджер реализуют один контракт. Дуплекс включает обратный
канал статусов (``report_status``) — для отрисовки реакции в мессенджере.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional, Protocol

from .models import (
    Capabilities,
    ChannelRef,
    DeliveryStatus,
    Message,
    RejectReason,
    SendResult,
)


class AdmissionPolicy(Protocol):
    """Политика допуска сообщений из мессенджера в очередь (точка расширения для модерации).

    Возвращает ``None`` — допустить; ``RejectReason`` — отклонить с этой причиной.
    """

    async def check(self, msg: Message) -> Optional[RejectReason]: ...


class Transport(ABC):
    id: str
    capabilities: Capabilities

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    async def run(self) -> None:
        """Монитор переподключения. По умолчанию — no-op (транспорт без реконнекта)."""

    # LoRa-адаптер транслирует msg.text как есть (ядро уже собрало префикс);
    # мессенджер-адаптер сам форматирует sender + text.
    # Для LoRa send() резолвится по commit узла, а не по записи в линк (AD-5/§5.1).
    @abstractmethod
    async def send(self, target: ChannelRef, msg: Message) -> SendResult: ...

    # Горячий мультикаст-поток входящих (§8).
    @abstractmethod
    def subscribe(self) -> AsyncIterator[Message]: ...

    # Обратный канал статусов. No-op, если supports_status_feedback=False.
    # reason заполняется только для REJECTED (см. RejectReason).
    @abstractmethod
    async def report_status(
        self,
        origin: ChannelRef,
        message_id: str,
        status: DeliveryStatus,
        reason: Optional[RejectReason] = None,
    ) -> None: ...
