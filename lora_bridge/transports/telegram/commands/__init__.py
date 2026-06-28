"""Подсистема транспорт-локальных команд Telegram (фасад пакета).

Разделена по оси изменчивости: стабильный каркас (``framework``) и растущий
список команд (``handlers``). Здесь — публичная поверхность для транспорта и
тестов; внутреннее устройство (на каком модуле что лежит) пакет скрывает.
"""

from __future__ import annotations

from .framework import CommandSpec, build_command_router, command_menu, render_help
from .handlers import COMMANDS

__all__ = [
    "COMMANDS",
    "CommandSpec",
    "build_command_router",
    "command_menu",
    "render_help",
]
