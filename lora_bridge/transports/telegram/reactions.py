"""Статус-фидбэк Telegram через реакции-эмодзи (§9/§10/§11).

Дуплекс: ядро шлёт ``DeliveryStatus`` на исходное сообщение → бот рисует реакцию.
PENDING откладывается дебаунсером (👀 не мелькает, если эфир ответил быстро),
SENT немедленно очищает. Карты эмодзи и логика дебаунса живут здесь; транспорт
только делегирует через ``ReactionFeedback.report``.

Только эмодзи из whitelist-а Telegram (иначе REACTION_INVALID); актуальный список —
в docstring ``aiogram.types.ReactionTypeEmoji.emoji``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import Bot
from aiogram.types import Message as TgMessage, ReactionTypeEmoji, ReactionTypeUnion

from ...domain.models import DeliveryStatus, RejectReason

log = logging.getLogger(__name__)

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
        self._sent: set[tuple[int, str]] = set()  # ключи где реакция реально выставлена

    def schedule(
        self,
        key: tuple[int, str],
        reaction: list[ReactionTypeUnion],
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

        API-вызов делается только если реакция была реально выставлена —
        иначе Telegram вернёт REACTION_EMPTY.
        """
        if prev := self._tasks.pop(key, None):
            prev.cancel()
            # Задача ещё спала — реакция не была выставлена, чистить нечего
            self._generation[key] = self._generation.get(key, -1) + 1
            return

        self._generation[key] = self._generation.get(key, -1) + 1

        if key not in self._sent:
            return  # реакция не выставлялась — REACTION_EMPTY если звонить

        self._sent.discard(key)
        chat_id, message_id = key
        try:
            await bot.set_message_reaction(chat_id, int(message_id), reaction=[])
        except Exception:  # noqa: BLE001
            log.debug("clear реакции не удался для %s/%s", chat_id, message_id, exc_info=True)

    async def _delayed_apply(
        self,
        key: tuple[int, str],
        gen: int,
        reaction: list[ReactionTypeUnion],
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
        first = reaction[0] if reaction else None
        emoji = first.emoji if isinstance(first, ReactionTypeEmoji) else "[]"
        log.debug("debouncer: выставляю реакцию %s на сообщение %s/%s", emoji, chat_id, message_id)
        try:
            await bot.set_message_reaction(chat_id, int(message_id), reaction=reaction)
            self._sent.add(key)  # реакция выставлена — теперь clear_now знает что чистить
        except Exception:  # noqa: BLE001
            log.debug("set_message_reaction не удался для %s/%s", chat_id, message_id, exc_info=True)


class ReactionFeedback:
    """Переводит ``DeliveryStatus`` в реакцию-эмодзи на исходном сообщении.

    Владеет дебаунсером и ботом; транспорт делегирует сюда весь ``report_status``.
    """

    def __init__(self, bot: Bot, delay: float = REACTION_DEBOUNCE_S) -> None:
        self._bot = bot
        self._debouncer = ReactionDebouncer(delay)

    async def report(
        self,
        chat_id: int,
        message_id: str,
        status: DeliveryStatus,
        reason: Optional[RejectReason] = None,
    ) -> None:
        if status == DeliveryStatus.TRANSMITTING:
            return  # промежуточный — не трогаем реакцию

        key = (chat_id, message_id)
        if status == DeliveryStatus.SENT:
            # Успех: немедленно отменить pending callback и убрать реакцию
            await self._debouncer.clear_now(key, self._bot)
            return

        # Для всех остальных статусов — откладываем через debouncer.
        # Если SENT придёт раньше чем истечёт задержка — 👀 вообще не появится.
        reaction = self._reaction_for(status, reason)
        if reaction:
            self._debouncer.schedule(key, reaction, self._bot)

    async def report_disabled(self, message: "TgMessage") -> None:
        """Реакция 🚫 на сообщение забаненного пользователя (best-effort)."""
        try:
            await self._bot.set_message_reaction(  # verify
                message.chat.id,
                message.message_id,
                reaction=[ReactionTypeEmoji(emoji="🚫")],
            )
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _reaction_for(
        status: DeliveryStatus, reason: Optional[RejectReason]
    ) -> list[ReactionTypeUnion]:
        if status == DeliveryStatus.PENDING:
            return [ReactionTypeEmoji(emoji=_PENDING_EMOJI)]
        emoji = REJECT_EMOJI.get(reason) if reason is not None else ERROR_EMOJI.get(status)
        return [ReactionTypeEmoji(emoji=emoji)] if emoji else []
