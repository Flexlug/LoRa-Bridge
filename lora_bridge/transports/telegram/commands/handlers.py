"""Конкретные команды бота — растущая часть подсистемы.

Добавить команду = написать хэндлер здесь и дописать строку ``CommandSpec`` в
``COMMANDS``. Каркас (``framework``) подхватит её в роутере, ``/help`` и меню
Telegram автоматически. Когда команд станет много — модуль дробится по фичам,
а ``COMMANDS`` собирается из них; каркас при этом не трогается.
"""

from __future__ import annotations

from aiogram.types import Message as TgMessage

from .framework import CommandSpec, render_help


async def ping(message: TgMessage) -> None:
    await message.answer("pong")


async def show_help(message: TgMessage) -> None:
    await message.answer(render_help(COMMANDS))


# Реестр команд бота. Порядок = порядок и в /help, и в меню Telegram.
COMMANDS: list[CommandSpec] = [
    CommandSpec("ping", "проверка живости бота", ping),
    CommandSpec("help", "список доступных команд", show_help),
]
