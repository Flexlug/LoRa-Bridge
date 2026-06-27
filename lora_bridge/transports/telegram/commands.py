"""Транспорт-локальные команды Telegram — «своя жизнь» бота вне общего pipeline.

Команды обрабатываются ЗДЕСЬ, на стороне транспорта, и НЕ публикуются в ``Hub`` —
значит не доходят до моста LoRa (``Bridge.admit``). Шов расширения: добавить
команду = добавить хэндлер в этот роутер.

Сеть неизвестных команд (``_ANY_COMMAND``) закрывает namespace: любое
command-shaped сообщение по грамматике aiogram (``/`` + ``[A-Za-z0-9_]``, не наивный
``startswith('/')``) либо обработано известным хэндлером, либо поймано сетью —
и в обоих случаях НЕ протекает в pipeline (принцип #10: инвариант закодирован
структурой + guard-тестом ``tests/test_telegram_commands.py``).

Роутер обязан включаться ДО bridge-хэндлера ``on_message`` (см. ``transport.py``):
Dispatcher пробует свои хэндлеры/дочерние роутеры в порядке включения.
"""

from __future__ import annotations

import logging
import re

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message as TgMessage

log = logging.getLogger(__name__)

# Любая команда по грамматике aiogram (имя из [A-Za-z0-9_] после '/').
_ANY_COMMAND = re.compile(r"[A-Za-z0-9_]+")

UNKNOWN_COMMAND_REPLY = "Неизвестная команда."


def build_command_router(transport_id: str) -> Router:
    """Роутер транспорт-локальных команд. Включать ДО bridge-хэндлера ``on_message``."""
    router = Router(name=f"telegram-commands:{transport_id}")

    @router.message(Command("ping"))
    async def ping(message: TgMessage) -> None:
        log.debug("транспорт '%s': /ping от %s", transport_id, message.chat.id)
        await message.answer("pong")

    # Сеть неизвестных команд — ПОСЛЕДНЯЯ в роутере (после всех известных),
    # но всё ещё до on_message. Закрывает namespace, чтобы команда не утекла.
    @router.message(Command(_ANY_COMMAND))
    async def unknown(message: TgMessage) -> None:
        log.debug("транспорт '%s': неизвестная команда %r", transport_id, message.text)
        await message.answer(UNKNOWN_COMMAND_REPLY)

    return router
