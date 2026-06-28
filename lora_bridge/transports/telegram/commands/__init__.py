"""Подсистема транспорт-локальных команд Telegram (фасад пакета).

Разделена по оси изменчивости: стабильный каркас (``framework``) и растущий
список команд (``handlers``). Здесь — публичная поверхность для транспорта и
тестов; внутреннее устройство (на каком модуле что лежит) пакет скрывает.
"""

from __future__ import annotations

from .framework import (
    CallbackSpec,
    CommandMeta,
    CommandSpec,
    build_command_router,
    command_menu,
    render_help,
)
from .handlers import BASIC_COMMAND_METAS, make_basic_commands
from .moderation import MODERATION_COMMAND_METAS, make_audit_callbacks, make_moderation_commands

ALL_COMMAND_METAS: list[CommandMeta] = BASIC_COMMAND_METAS + MODERATION_COMMAND_METAS

__all__ = [
    "ALL_COMMAND_METAS",
    "BASIC_COMMAND_METAS",
    "CallbackSpec",
    "CommandMeta",
    "CommandSpec",
    "build_command_router",
    "command_menu",
    "make_audit_callbacks",
    "make_basic_commands",
    "make_moderation_commands",
    "render_help",
]
