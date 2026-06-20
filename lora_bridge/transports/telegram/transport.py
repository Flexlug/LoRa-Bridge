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
# Актуальный список — в docstring aiogram.types.ReactionTypeEmoji.emoji.
_PENDING_EMOJI = "👀"   # бот принял сообщение, ждёт отправки в LoRa

ERROR_EMOJI: dict[DeliveryStatus, str] = {
    DeliveryStatus.FAILED: "😢",   # ошибка TX — важно показать
    DeliveryStatus.UNKNOWN: "🤔",  # статус неизвестен после рестарта
}
REJECT_EMOJI: dict[RejectReason, str] = {
    RejectReason.TOO_LONG: "🤨",    # сообщение не влезло
    RejectReason.RATE_LIMIT: "🥱",  # эфир перегружен
    RejectReason.TTL_EXPIRED: "😴", # протухло в очереди
}

# Задержка перед простановкой реакции (секунды).
# Если сообщение отправилось быстрее этого порога — 👀 вообще не появится.
# MeshCore MSG_OK для public/private каналов приходит за 0.5–1.5с;
# 2.0с даёт запас чтобы 👀 не мелькал при нормальной работе.
REACTION_DEBOUNCE_S = 2.0


class ReactionDebouncer:
    """Откладывает простановку реакции; SENT немедленно очищает.

    Защита от гонки: generation-счётчик на каждый (chat_id, message_id).
    Даже если callback «убежал» мимо cancel() и дошёл до await — он
    проверяет generation и отказывается, если SENT уже сменил его.

    Ключ — (chat_id, message_id) чтобы не было коллизий между чатами.
    """

    def __init__(self, delay: float = REACTION_DEBOUNCE_S) -> None:
        self._delay = delay
        self._tasks: dict[tuple[int, str], asyncio.Task[None]] = {}
        self._generation: dict[tuple[int, str], int] = {}

    def schedule(
        self,
        key: tuple[int, str],
        reaction: list[ReactionTypeEmoji],
        bot: Bot,
    ) -> None:
        """Запланировать реакцию с задержкой. Отменяет предыдущий callback для этого ключа."""
        if prev := self._tasks.pop(key, None):
            prev.cancel()
        gen = self._generation.get(key, -1) + 1
        self._generation[key] = gen
        self._tasks[key] = asyncio.create_task(
            self._delayed_apply(key, gen, reaction, bot)
        )

    async def clear_now(self, key: tuple[int, str], bot: Bot) -> None:
        """Немедленно убрать реакцию (SENT). Отменяет любой отложенный callback.

        Инкрементирует generation чтобы callback, уже выполняющийся за await,
        не смог выставить реакцию после того как мы её очистили.
        """
        if prev := self._tasks.pop(key, None):
            prev.cancel()
        self._generation[key] = self._generation.get(key, -1) + 1
        chat_id, message_id = key
        try:
            await bot.set_message_reaction(chat_id, int(message_id), reaction=[])
        except Exception:  # noqa: BLE001
            log.debug("clear реакции не удался для %s/%s", chat_id, message_id, exc_info=True)

    async def _delayed_apply(
        self,
        key: tuple[int, str],
        gen: int,
        reaction: list[ReactionTypeEmoji],
        bot: Bot,
    ) -> None:
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            return

        # Проверяем generation после пробуждения — не пришёл ли SENT пока спали
        if self._generation.get(key) != gen:
            return

        self._tasks.pop(key, None)
        chat_id, message_id = key
        try:
            await bot.set_message_reaction(chat_id, int(message_id), reaction=reaction)
        except Exception:  # noqa: BLE001
            log.debug("set_message_reaction не удался для %s/%s", chat_id, message_id, exc_info=True)


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
        self._debouncer = ReactionDebouncer()

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
            text = msg.text
        else:
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
        if status == DeliveryStatus.TRANSMITTING:
            return  # промежуточный — не трогаем реакцию

        chat_id, _ = split_channel(origin.channel)
        key = (chat_id, message_id)

        if status == DeliveryStatus.SENT:
            # Успех: немедленно отменить pending callback и убрать реакцию
            await self._debouncer.clear_now(key, self._bot)
            return

        # Для всех остальных статусов — откладываем через debouncer.
        # Если SENT придёт раньше чем истечёт задержка — 👀 вообще не появится.
        if status == DeliveryStatus.PENDING:
            reaction: list[ReactionTypeEmoji] = [ReactionTypeEmoji(emoji=_PENDING_EMOJI)]
        elif reason is not None:
            emoji = REJECT_EMOJI.get(reason)
            reaction = [ReactionTypeEmoji(emoji=emoji)] if emoji else []
        else:
            emoji = ERROR_EMOJI.get(status)
            reaction = [ReactionTypeEmoji(emoji=emoji)] if emoji else []

        if reaction:
            self._debouncer.schedule(key, reaction, self._bot)
