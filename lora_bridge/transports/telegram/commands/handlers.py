"""Конкретные базовые команды бота — растущая часть подсистемы.

Добавить команду = написать хэндлер здесь и дописать строку в
``BASIC_COMMAND_METAS`` / ``make_basic_commands``. Каркас (``framework``)
подхватит её в роутере, ``/help`` и меню Telegram автоматически.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram.types import Message as TgMessage

from .framework import CommandMeta, CommandSpec, render_help
from ..moderation.roles import Role

if TYPE_CHECKING:
    from ..moderation.store import ModerationStore

# Статические метаданные для документации и /help; без хендлеров.
BASIC_COMMAND_METAS: list[CommandMeta] = [
    CommandMeta("help", "список доступных команд", Role.USER),
]


def make_basic_commands(
    store: "ModerationStore",
    owner_id: int,
    all_metas: list[CommandMeta],
) -> list[CommandSpec]:
    """Фабрика базовых команд (/help) с замыканием над store."""

    async def _show_help(message: TgMessage) -> None:
        uid = message.from_user.id if message.from_user else 0
        role = await store.get_role(owner_id, uid)
        visible = [m for m in all_metas if m.min_role <= role]
        await message.reply(render_help(visible, role), parse_mode="HTML")

    return [
        CommandSpec("help", "список доступных команд", Role.USER, _show_help),
    ]
