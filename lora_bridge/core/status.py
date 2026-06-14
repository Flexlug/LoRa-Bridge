"""Диспетчер статусов: ядро → ``Transport.report_status`` (§10).

Каждый переход DeliveryStatus превращается в реакцию-индикатор на ИСХОДНОМ
сообщении в мессенджере-источнике. No-op для транспортов без поддержки фидбека
(LoRa: supports_status_feedback=False).
"""

from __future__ import annotations

from ..domain.models import ChannelRef, DeliveryStatus, RejectReason
from ..domain.ports import Transport


class StatusDispatcher:
    def __init__(self, transports: dict[str, Transport]) -> None:
        self._transports = transports

    async def set(
        self,
        origin: ChannelRef,
        message_id: str,
        status: DeliveryStatus,
        reason: RejectReason | None = None,
    ) -> None:
        """Отрисовать статус на исходном сообщении (идемпотентно — для recovery, §11.1)."""
        transport = self._transports.get(origin.transport_id)
        if transport is None or not transport.capabilities.supports_status_feedback:
            return
        # TODO(§10): корреляция message_id ↔ реакция; маппинг статус→эмодзи.
        await transport.report_status(origin, message_id, status, reason)
