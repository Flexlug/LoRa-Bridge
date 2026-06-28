"""Каркас транспорт-локальных команд Telegram — стабильная часть подсистемы.

Здесь живёт всё, что НЕ меняется при добавлении команды: контракт ``CommandSpec``,
сборка роутера (``build_command_router``), сеть неизвестных команд и проекции
реестра в текст ``/help`` (``render_help``) и меню Telegram (``command_menu``).

Сами команды — в ``handlers`` (растущая часть). Импорт односторонний
(``handlers`` → ``framework``): каркас о конкретных командах не знает, поэтому
проекции принимают реестр аргументом, а не читают глобальный список.

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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import BotCommand
from aiogram.types import CallbackQuery
from aiogram.types import Message as TgMessage

if TYPE_CHECKING:
    from ..moderation.roles import Role
    from ..moderation.store import ModerationStore

log = logging.getLogger(__name__)

# Любая команда по грамматике aiogram (имя из [A-Za-z0-9_] после '/').
_ANY_COMMAND = re.compile(r"[A-Za-z0-9_]+")

UNKNOWN_COMMAND_REPLY = "Неизвестная команда."
INSUFFICIENT_RIGHTS_REPLY = "Недостаточно прав."

CommandHandler = Callable[[TgMessage], Awaitable[None]]
CallbackHandler = Callable[[CallbackQuery], Awaitable[None]]


@dataclass(frozen=True)
class CommandMeta:
    """Метаданные команды без хендлера — для документации и /help."""

    name: str
    description: str
    min_role: "Role"


@dataclass(frozen=True)
class CommandSpec(CommandMeta):
    """Полная спецификация команды: метаданные + хендлер."""

    handler: CommandHandler = field(repr=False)


@dataclass(frozen=True)
class CallbackSpec:
    """Спецификация callback_query хендлера (пагинация и т.п.)."""

    prefix: str
    handler: CallbackHandler
    min_role: "Role"


def render_help(commands: list[CommandMeta]) -> str:
    """Текст ``/help`` из переданного (уже отфильтрованного) реестра."""
    lines = [f"/{spec.name} — {spec.description}" for spec in commands]
    return "Доступные команды:\n" + "\n".join(lines)


def command_menu(commands: list[CommandMeta], role: "Role") -> list[BotCommand]:
    """Меню для ``Bot.set_my_commands`` — фильтрует по роли вызывающего."""
    visible = [c for c in commands if c.min_role <= role]
    return [BotCommand(command=spec.name, description=spec.description) for spec in visible]


def build_command_router(
    transport_id: str,
    commands: list[CommandSpec],
    store: "ModerationStore | None" = None,
    owner_id: int = 0,
    callbacks: list[CallbackSpec] | None = None,
) -> Router:
    """Роутер транспорт-локальных команд. Включать ДО bridge-хэндлера ``on_message``."""
    router = Router(name=f"telegram-commands:{transport_id}")

    for spec in commands:
        _spec = spec

        if store is not None and _spec.min_role.value > 0:
            async def _checked(message: TgMessage, __spec: CommandSpec = _spec) -> None:
                uid = message.from_user.id if message.from_user else 0
                role = await store.get_role(owner_id, uid)
                if role < __spec.min_role:
                    await message.answer(INSUFFICIENT_RIGHTS_REPLY)
                    return
                await __spec.handler(message)

            router.message.register(_checked, Command(_spec.name))
        else:
            router.message.register(_spec.handler, Command(_spec.name))

    if callbacks:
        for cb in callbacks:
            _cb = cb

            from aiogram import F as _F

            async def _cb_checked(
                query: CallbackQuery, __cb: CallbackSpec = _cb
            ) -> None:
                if store is not None:
                    uid = query.from_user.id if query.from_user else 0
                    role = await store.get_role(owner_id, uid)
                    if role < __cb.min_role:
                        await query.answer(INSUFFICIENT_RIGHTS_REPLY)
                        return
                await __cb.handler(query)

            router.callback_query.register(
                _cb_checked,
                _F.data.startswith(_cb.prefix),
            )

    # Сеть неизвестных команд — ПОСЛЕДНЯЯ в роутере (после всех известных),
    # но всё ещё до on_message. Закрывает namespace, чтобы команда не утекла.
    @router.message(Command(_ANY_COMMAND))
    async def unknown(message: TgMessage) -> None:
        log.debug("транспорт '%s': неизвестная команда %r", transport_id, message.text)
        await message.answer(UNKNOWN_COMMAND_REPLY)

    return router
