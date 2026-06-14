"""Типы эндпоинтов MeshCore (§5.1).

Discriminated union по ``type`` — конфиг самодокументируем, нет скрытых правил
вида «есть pubkey ⇒ room server».
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field


class PublicEndpoint(BaseModel):
    type: Literal["public"]  # Public channel — общий PSK, flood без ACK
    channel_name: str  # Имя канала в MeshCore (вкладка Channels в приложении)


class PrivateEndpoint(BaseModel):
    type: Literal["private"]  # Channel со своим secret, flood без ACK
    channel_name: str  # Имя канала в MeshCore (вкладка Channels в приложении)
    secret: str


class RoomServerEndpoint(BaseModel):
    type: Literal["room_server"]  # Room Server — direct + login, реальный ACK + backfill
    pubkey: str
    password: Optional[str] = None  # пусто → read-only (постинг недоступен)


Endpoint = Annotated[
    Union[PublicEndpoint, PrivateEndpoint, RoomServerEndpoint],
    Field(discriminator="type"),
]
