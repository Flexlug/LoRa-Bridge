"""Конфиги мессенджеров — дискриминированный union по ``kind``.

Симметрично ``LoraNode``: новый мессенджер = новый класс + расширение Union.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field

from .ids import MessengerId


class BaseMessengerConfig(BaseModel):
    """Поля, общие для всех мессенджеров (доступны до isinstance-сужения)."""

    id: MessengerId = Field(
        description=(
            "Уникальный id транспорта мессенджера. На него ссылается "
            "``rooms[].subscribers[].transport``."
        )
    )
    kind: str = Field(
        description="Тип мессенджера. Перекрывается ``Literal`` в подклассах.",
    )
    tag: Optional[str] = Field(
        default=None,
        description=(
            "Переопределение тега источника в префиксе ``[тип:ник]`` при выгрузке "
            "в LoRa. По умолчанию — заглавные первых двух букв ``kind`` (например, "
            '``telegram`` → ``"TG"``).'
        ),
    )


class TelegramCommandsConfig(BaseModel):
    """Опциональный блок команд Telegram-бота. Отсутствие = команды выключены."""

    owner_id: int = Field(description="Telegram user ID владельца бота (роль OWNER).")
    alias_max_chars: int = Field(
        default=16,
        description="Максимальная длина псевдонима пользователя.",
    )


class TelegramMessengerConfig(BaseMessengerConfig):
    """Конфиг Telegram-бота.

    !!! note
        У бота должен быть **отключён** privacy mode (BotFather → ``/setprivacy``
        → ``Disable``), иначе он не видит сообщения в группах, только команды.
    """

    kind: Literal["telegram"] = Field(
        description="Тег дискриминатора — должно быть ``telegram``."
    )
    token: str = Field(description="Telegram Bot API token, выданный BotFather.")
    commands: Optional[TelegramCommandsConfig] = Field(
        default=None,
        description="Блок команд; отсутствие или null отключает командный роутер.",
    )


MessengerConfig = Annotated[
    Union[TelegramMessengerConfig],  # расширять Union при добавлении мессенджеров
    Field(discriminator="kind"),
]
"""Конфиг одного мессенджер-транспорта.

Дискриминирован по ``kind``. Расширяется при добавлении нового мессенджера.
"""
