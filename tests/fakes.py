"""In-memory транспорт для тестов пайплайна (реализует порт Transport)."""
from __future__ import annotations

from typing import AsyncIterator, Optional

from lora_bridge.transports.hub import Hub
from lora_bridge.domain.models import (
    Capabilities,
    ChannelRef,
    DeliveryStatus,
    Message,
    RateSpec,
    RejectReason,
    SendResult,
)

LORA_CAPS = Capabilities(
    max_text_bytes=150, egress_rate=RateSpec(100, 60),
    supports_status_feedback=False, emits_tx_done=False,
)
MSG_CAPS = Capabilities(
    max_text_bytes=4096, egress_rate=RateSpec(100, 60),
    supports_status_feedback=True, emits_tx_done=False,
)


class FakeTransport:
    def __init__(self, id: str, capabilities: Capabilities, *, fail: bool = False,
                 busy_times: int = 0) -> None:
        self.id = id
        self.capabilities = capabilities
        self._hub = Hub()
        self.sent: list[tuple[ChannelRef, Message]] = []
        self.statuses: list[tuple[str, DeliveryStatus, Optional[RejectReason]]] = []
        self.notices: list[Message] = []
        self.started = False
        self._fail = fail
        self._busy_left = busy_times

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.started = False

    async def send(self, target: ChannelRef, msg: Message) -> SendResult:
        if self._busy_left > 0:
            self._busy_left -= 1
            return SendResult.overloaded()
        if self._fail:
            return SendResult.failure("fake fail")
        self.sent.append((target, msg))
        return SendResult.success()

    def subscribe(self) -> AsyncIterator[Message]:
        return self._hub.subscribe()

    async def report_status(self, origin: ChannelRef, message_id: str,
                            status: DeliveryStatus,
                            reason: Optional[RejectReason] = None) -> None:
        self.statuses.append((message_id, status, reason))

    async def inject(self, msg: Message) -> None:
        await self._hub.publish(msg)
