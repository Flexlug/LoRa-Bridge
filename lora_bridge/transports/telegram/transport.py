"""Telegram-адаптер: порт ``Transport`` поверх ``aiogram``.

Особенности (§9/§10/§11): фильтрация по топику (forum thread), реакции-статусы
(``setMessageReaction``), отключённый privacy mode у бота. Канал-эндпоинт
кодируется как ``chat`` или ``chat#topic`` (см. ``messenger_channel``).

Без живого токена адаптер не проверялся — места вызовов API помечены ``# verify``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional, TYPE_CHECKING

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message as TgMessage, ReactionTypeEmoji

from ..hub import Hub
from ...domain.ports import Transport
from ...domain.models import (
    BRIDGE_TRANSPORT_UID,
    LORA_SENDER_UID,
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

if TYPE_CHECKING:
    from ...config.schema import TelegramMessengerConfig

log = logging.getLogger(__name__)

# Только эмодзи из whitelist-а Telegram (REACTION_INVALID иначе).
# Полный список: https://core.telegram.org/bots/api#reactiontypeemoji
STATUS_EMOJI: dict[DeliveryStatus, str] = {
    DeliveryStatus.PENDING: "🕊",       # ждёт в очереди
    DeliveryStatus.TRANSMITTING: "⚡",   # передаётся в эфир
    DeliveryStatus.SENT: "👍",           # коммит подтверждён
    DeliveryStatus.FAILED: "😢",         # ошибка TX
    DeliveryStatus.UNKNOWN: "🤔",        # статус неизвестен после рестарта
}
REJECT_EMOJI: dict[RejectReason, str] = {
    RejectReason.TOO_LONG: "🤨",        # сообщение не влезло
    RejectReason.RATE_LIMIT: "🥱",      # эфир перегружен
    RejectReason.TTL_EXPIRED: "😴",     # протухло в очереди
}


def split_channel(channel: str) -> tuple[int, Optional[int]]:
    """``"chat"`` / ``"chat#topic"`` → (chat_id, thread_id|None)."""
    if "#" in channel:
        chat, topic = channel.split("#", 1)
        return int(chat), int(topic)
    return int(channel), None


class TelegramTransport(Transport):
    capabilities = Capabilities(
        max_text_bytes=4096,
        egress_rate=RateSpec(20, 60, burst=20),
        supports_status_feedback=True,
        emits_tx_done=False,
    )

    _poll_task: asyncio.Task[None] | None = None

    def __init__(self, transport_id: str, config: TelegramMessengerConfig) -> None:
        self.id = transport_id
        self._hub = Hub()
        self._bot = Bot(config.token)
        self._dp = Dispatcher()
        self._dp.message.register(self.on_message, F.text)  # verify: фильтр текстовых

    async def start(self) -> None:
        me = await self._bot.get_me()  # verify: бот доступен (sanity)
        log.info("Telegram-транспорт '%s': бот @%s (id=%d) подключён", self.id, me.username, me.id)
        self._poll_task = asyncio.create_task(
            self._dp.start_polling(self._bot, handle_signals=False)  # verify
        )

    async def stop(self) -> None:
        await self._dp.stop_polling()  # verify
        if self._poll_task is not None:
            self._poll_task.cancel()
        await self._bot.close()  # verify

    async def on_message(self, message: TgMessage) -> None:
        await self._hub.publish(self.normalize(message))

    def normalize(self, message: TgMessage) -> Message:
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
        chat_id, thread_id = split_channel(target.channel)
        if msg.sender.transport_uid in (BRIDGE_TRANSPORT_UID, LORA_SENDER_UID):
            # уведомления моста и сообщения из эфира — текст как есть
            text = msg.text
        else:
            # сообщение от пользователя мессенджера — добавляем имя
            text = f"<b>{msg.sender.display_name}</b>: {msg.text}"
        try:
            await self._bot.send_message(  # verify
                chat_id, text, message_thread_id=thread_id, parse_mode="HTML"
            )
            return SendResult.success()
        except Exception as exc:  # noqa: BLE001
            log.exception("Telegram send в %s упал", target.channel)
            return SendResult.failure(str(exc))

    def subscribe(self) -> AsyncIterator[Message]:
        return self._hub.subscribe()

    async def report_status(
        self,
        origin: ChannelRef,
        message_id: str,
        status: DeliveryStatus,
        reason: Optional[RejectReason] = None,
    ) -> None:
        emoji = REJECT_EMOJI.get(reason) if reason else STATUS_EMOJI.get(status)
        if emoji is None or self._bot is None:
            return
        chat_id, _ = split_channel(origin.channel)
        try:
            await self._bot.set_message_reaction(  # verify; идемпотентно (§11.1)
                chat_id, int(message_id), reaction=[ReactionTypeEmoji(emoji=emoji)]
            )
        except Exception:  # noqa: BLE001
            log.debug("set_message_reaction не удался для %s", message_id, exc_info=True)
