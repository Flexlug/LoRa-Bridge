"""Транспорт-локальные команды Telegram — «своя жизнь» бота вне общего pipeline.

Команды обрабатываются ЗДЕСЬ, на стороне транспорта, и НЕ публикуются в ``Hub`` —
значит не доходят до моста LoRa (``Bridge.admit``).

Реестр ``COMMANDS`` — единственный источник правды; из него питаются три потребителя:
регистрация хэндлеров в роутере (``build_command_router``), текст ``/help``
(``render_help``) и меню Telegram (``command_menu`` → ``Bot.set_my_commands``).
Шов расширения: добавить команду = добавить строку ``CommandSpec`` в ``COMMANDS``.

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
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BotCommand
from aiogram.types import Message as TgMessage

log = logging.getLogger(__name__)

# Любая команда по грамматике aiogram (имя из [A-Za-z0-9_] после '/').
_ANY_COMMAND = re.compile(r"[A-Za-z0-9_]+")

UNKNOWN_COMMAND_REPLY = "Неизвестная команда."

CommandHandler = Callable[[TgMessage], Awaitable[None]]


@dataclass(frozen=True)
class CommandSpec:
    """Одна транспорт-локальная команда: строка реестра ``COMMANDS``.

    ``name`` — имя без ведущего ``/`` (грамматика aiogram); ``description`` идёт и в
    ``/help``, и в меню Telegram; ``handler`` — корутина-обработчик aiogram.
    """

    name: str
    description: str
    handler: CommandHandler


async def ping(message: TgMessage) -> None:
    await message.answer("pong")


async def show_help(message: TgMessage) -> None:
    await message.answer(render_help())


# Реестр команд. Порядок = порядок и в /help, и в меню Telegram.
COMMANDS: list[CommandSpec] = [
    CommandSpec("ping", "проверка живости бота", ping),
    CommandSpec("help", "список доступных команд", show_help),
]


def render_help() -> str:
    """Текст ``/help`` из реестра — описания берутся из единого ``COMMANDS``."""
    lines = [f"/{spec.name} — {spec.description}" for spec in COMMANDS]
    return "Доступные команды:\n" + "\n".join(lines)


def command_menu() -> list[BotCommand]:
    """Меню для ``Bot.set_my_commands`` — из того же ``COMMANDS``, без дрейфа."""
    return [BotCommand(command=spec.name, description=spec.description) for spec in COMMANDS]


def build_command_router(transport_id: str) -> Router:
    """Роутер транспорт-локальных команд. Включать ДО bridge-хэндлера ``on_message``."""
    router = Router(name=f"telegram-commands:{transport_id}")

    # Известные команды из реестра — в объявленном порядке.
    for spec in COMMANDS:
        router.message.register(spec.handler, Command(spec.name))

    # Сеть неизвестных команд — ПОСЛЕДНЯЯ в роутере (после всех известных),
    # но всё ещё до on_message. Закрывает namespace, чтобы команда не утекла.
    @router.message(Command(_ANY_COMMAND))
    async def unknown(message: TgMessage) -> None:
        log.debug("транспорт '%s': неизвестная команда %r", transport_id, message.text)
        await message.answer(UNKNOWN_COMMAND_REPLY)

    return router
