from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiogram.types import Message as TgMessage


async def delete_after(delay: float, *messages: "TgMessage") -> None:
    await asyncio.sleep(delay)
    for msg in messages:
        with suppress(Exception):
            await msg.delete()
