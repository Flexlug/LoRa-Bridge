"""Telegram-адаптер: порт ``Transport`` поверх ``aiogram``.

Особенности (§9/§10/§11): фильтрация по топику (forum thread), реакции-статусы
(``setMessageReaction``), отключённый privacy mode у бота. Канал-эндпоинт
кодируется как ``chat`` или ``chat#topic`` (см. ``messenger_channel``).

ВНИМАНИЕ: импорт ``aiogram`` ленивый (пакет/ядро импортируются без него). Без
живого токена адаптер не проверялся — места вызовов API помечены ``# verify``.
"""
from __future__ import annotations

import logging
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
    messenger_channel,
)
from ..hub import Hub

if TYPE_CHECKING:
    from ...config.schema import MessengerConfig

log = logging.getLogger(__name__)

# Статус → эмодзи-реакция (§10).
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


def _split_channel(channel: str) -> tuple[int, Optional[int]]:
    """``"chat"`` / ``"chat#topic"`` → (chat_id, thread_id|None)."""
    if "#" in channel:
        chat, topic = channel.split("#", 1)
        return int(chat), int(topic)
    return int(channel), None


class TelegramTransport:
    capabilities = Capabilities(
        max_text_bytes=4096,
        egress_rate=RateSpec(20, 60, burst=20),
        supports_status_feedback=True,
        emits_tx_done=False,
    )

    def __init__(self, transport_id: str, tag: str, config: "MessengerConfig") -> None:
        self.id = transport_id
        self.tag = tag
        self._token = config.token
        self._hub = Hub()
        self._bot = None
        self._dp = None
        self._poll_task = None

    async def start(self) -> None:
        import asyncio

        from aiogram import Bot, Dispatcher, F                # lazy import
        from aiogram.types import Message as TgMessage

        self._bot = Bot(self._token)
        self._dp = Dispatcher()

        @self._dp.message(F.text)                             # verify: фильтр текстовых
        async def _on_message(message: "TgMessage") -> None:
            await self._hub.publish(self._normalize(message))

        await self._bot.get_me()                              # verify: бот доступен (sanity)
        # long-polling — собственный цикл aiogram; держим фоновой задачей.
        self._poll_task = asyncio.create_task(
            self._dp.start_polling(self._bot, handle_signals=False)   # verify
        )

    async def stop(self) -> None:
        if self._dp is not None:
            await self._dp.stop_polling()                     # verify
        if self._poll_task is not None:
            self._poll_task.cancel()
        if self._bot is not None:
            await self._bot.session.close()

    def _normalize(self, message) -> Message:
        thread = getattr(message, "message_thread_id", None)
        chat_id = str(message.chat.id)
        user = message.from_user
        return Message(
            id=str(message.message_id),
            source=ChannelRef(self.id, messenger_channel(chat_id, str(thread) if thread else None)),
            sender=Identity(
                display_name=(user.full_name if user else "unknown"),
                transport_uid=str(user.id) if user else "0",
            ),
            text=message.text or "",
        )

    async def send(self, target: ChannelRef, msg: Message) -> SendResult:
        chat_id, thread_id = _split_channel(target.channel)
        # системные уведомления моста идут как есть; зеркала — с автором
        text = msg.text if msg.sender.transport_uid == "__bridge__" else (
            f"<b>{msg.sender.display_name}</b>: {msg.text}"
        )
        try:
            await self._bot.send_message(                     # verify
                chat_id, text, message_thread_id=thread_id, parse_mode="HTML"
            )
            return SendResult.success()
        except Exception as exc:                              # noqa: BLE001
            log.exception("Telegram send в %s упал", target.channel)
            return SendResult.failure(str(exc))

    def subscribe(self) -> AsyncIterator[Message]:
        return self._hub.subscribe()

    async def report_status(
        self, origin: ChannelRef, message_id: str,
        status: DeliveryStatus, reason: Optional[RejectReason] = None,
    ) -> None:
        emoji = REJECT_EMOJI.get(reason) if reason else STATUS_EMOJI.get(status)
        if emoji is None or self._bot is None:
            return
        chat_id, _ = _split_channel(origin.channel)
        try:
            from aiogram.types import ReactionTypeEmoji
            await self._bot.set_message_reaction(             # verify; идемпотентно (§11.1)
                chat_id, int(message_id), reaction=[ReactionTypeEmoji(emoji=emoji)]
            )
        except Exception:                                     # noqa: BLE001
            log.debug("set_message_reaction не удался для %s", message_id, exc_info=True)
