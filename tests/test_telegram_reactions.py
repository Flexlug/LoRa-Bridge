"""Характеризационные тесты статус-фидбэка Telegram (reactions.py).

Закрепляют поведение ``ReactionDebouncer`` и маппинг ``DeliveryStatus → реакция``
(``ReactionFeedback``), вынесенных из transport.py при разбиении монолита.
Цель — зафиксировать поведение ровно таким, каким оно было до разбиения:
дебаунс PENDING, немедленная очистка на SENT, выбор эмодзи по статусу/причине.

``Bot`` подменяется ``AsyncMock`` — на железо/токен не ходим.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from aiogram.types import ReactionTypeEmoji

from lora_bridge.domain.models import DeliveryStatus, RejectReason
from lora_bridge.transports.telegram.reactions import (
    REJECT_EMOJI,
    _PENDING_EMOJI,
    ReactionDebouncer,
    ReactionFeedback,
)


def _bot() -> AsyncMock:
    return AsyncMock()


# --- ReactionDebouncer: дебаунс и очистка ----------------------------------


async def test_schedule_applies_reaction_after_delay() -> None:
    bot = _bot()
    deb = ReactionDebouncer(delay=0.01)
    deb.schedule((111, "5"), [ReactionTypeEmoji(emoji=_PENDING_EMOJI)], bot)
    await asyncio.sleep(0.05)

    bot.set_message_reaction.assert_awaited_once()
    args, kwargs = bot.set_message_reaction.call_args
    assert args[0] == 111
    assert args[1] == 5  # int(message_id)
    assert kwargs["reaction"][0].emoji == _PENDING_EMOJI


async def test_clear_now_cancels_pending_before_applied() -> None:
    bot = _bot()
    deb = ReactionDebouncer(delay=10.0)  # длинная задержка — выставиться не успеет
    deb.schedule((111, "5"), [ReactionTypeEmoji(emoji=_PENDING_EMOJI)], bot)
    await deb.clear_now((111, "5"), bot)
    await asyncio.sleep(0.02)

    # реакция не выставлялась и чистить нечего → ни одного вызова API
    bot.set_message_reaction.assert_not_awaited()


async def test_clear_now_removes_already_applied_reaction() -> None:
    bot = _bot()
    deb = ReactionDebouncer(delay=0.01)
    deb.schedule((111, "5"), [ReactionTypeEmoji(emoji=_PENDING_EMOJI)], bot)
    await asyncio.sleep(0.05)  # дать реакции выставиться
    bot.set_message_reaction.reset_mock()

    await deb.clear_now((111, "5"), bot)
    bot.set_message_reaction.assert_awaited_once()
    _, kwargs = bot.set_message_reaction.call_args
    assert kwargs["reaction"] == []  # очистка = пустой список


async def test_generation_guard_sent_during_apply_window() -> None:
    """Generation-счётчик: SENT после пробуждения callback не оставляет 👀 выставленной.

    Гонка: callback проснулся после debounce, но clear_now успевает сменить
    generation до/около API-вызова. Точное число вызовов зависит от timing,
    но финальное состояние всегда — очищено ([]).
    """
    order: list[list[object]] = []

    async def fake_react(*args: object, reaction: list[object], **kw: object) -> None:
        order.append(reaction)

    bot = _bot()
    bot.set_message_reaction.side_effect = fake_react

    deb = ReactionDebouncer(delay=0.05)
    deb.schedule((1, "99"), [ReactionTypeEmoji(emoji=_PENDING_EMOJI)], bot)
    await asyncio.sleep(0.06)  # callback проснулся
    await deb.clear_now((1, "99"), bot)
    await asyncio.sleep(0.05)

    assert order[-1] == [], f"последний вызов должен быть clear, получили: {order}"


# --- ReactionFeedback: маппинг статуса в реакцию ---------------------------


async def test_transmitting_is_noop() -> None:
    bot = _bot()
    fb = ReactionFeedback(bot, delay=0.01)
    await fb.report(111, "5", DeliveryStatus.TRANSMITTING)
    await asyncio.sleep(0.03)
    bot.set_message_reaction.assert_not_awaited()


async def test_pending_schedules_eyes() -> None:
    bot = _bot()
    fb = ReactionFeedback(bot, delay=0.01)
    await fb.report(111, "5", DeliveryStatus.PENDING)
    await asyncio.sleep(0.05)
    _, kwargs = bot.set_message_reaction.call_args
    assert kwargs["reaction"][0].emoji == _PENDING_EMOJI


async def test_rejected_uses_reject_emoji() -> None:
    bot = _bot()
    fb = ReactionFeedback(bot, delay=0.01)
    await fb.report(111, "5", DeliveryStatus.REJECTED, RejectReason.TOO_LONG)
    await asyncio.sleep(0.05)
    _, kwargs = bot.set_message_reaction.call_args
    assert kwargs["reaction"][0].emoji == REJECT_EMOJI[RejectReason.TOO_LONG]


async def test_sent_clears_applied_reaction() -> None:
    bot = _bot()
    fb = ReactionFeedback(bot, delay=0.01)
    await fb.report(111, "5", DeliveryStatus.PENDING)
    await asyncio.sleep(0.05)  # 👀 выставлен
    bot.set_message_reaction.reset_mock()

    await fb.report(111, "5", DeliveryStatus.SENT)
    bot.set_message_reaction.assert_awaited_once()
    _, kwargs = bot.set_message_reaction.call_args
    assert kwargs["reaction"] == []
