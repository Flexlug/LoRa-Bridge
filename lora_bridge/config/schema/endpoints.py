"""Типы эндпоинтов MeshCore (§5.1 архитектуры).

Дискриминированный union по полю ``type`` делает конфиг самодокументируемым:
никаких скрытых правил вида «есть pubkey ⇒ room server» — структура
определяется явным тегом.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field


class PublicEndpoint(BaseModel):
    """Публичный канал MeshCore (общий PSK, flood без ACK).

    Подходит для общего чата. Доставка не гарантируется.
    """

    type: Literal["public"] = Field(description="Тег дискриминатора — должно быть ``public``.")
    channel_name: str = Field(
        description="Имя канала из вкладки Channels в приложении MeshCore."
    )


class PrivateEndpoint(BaseModel):
    """Приватный канал MeshCore (собственный PSK, flood без ACK).

    Подходит для закрытых рабочих групп. Доставка не гарантируется (flood-режим).
    """

    type: Literal["private"] = Field(description="Тег дискриминатора — должно быть ``private``.")
    channel_name: str = Field(
        description="Имя канала из вкладки Channels в приложении MeshCore."
    )
    secret: str = Field(description="PSK канала из настроек MeshCore.")


class RoomServerEndpoint(BaseModel):
    """Room Server — адресная доставка с реальным ACK и backfill.

    В отличие от ``public``/``private`` гарантирует доставку (есть delivery-ACK
    ``0x82``) и подтягивает пропущенные сообщения при переподключении.
    """

    type: Literal["room_server"] = Field(
        description="Тег дискриминатора — должно быть ``room_server``."
    )
    pubkey: str = Field(
        description="Публичный ключ Room Server из приложения MeshCore."
    )
    password: Optional[str] = Field(
        default=None,
        description=(
            "Гостевой пароль. Если опущен — доступ read-only (постинг недоступен)."
        ),
    )


Endpoint = Annotated[
    Union[PublicEndpoint, PrivateEndpoint, RoomServerEndpoint],
    Field(discriminator="type"),
]
"""Тип LoRa-эндпоинта в MeshCore-ноде.

Дискриминирован по ``type``: ``public`` | ``private`` | ``room_server``.
См. §5.1 архитектуры — для каждого типа commit имеет разную семантику.
"""
