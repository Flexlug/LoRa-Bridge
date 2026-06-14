"""Конфиги мессенджеров — discriminated union по ``kind``, симметрично LoraNode.

Для добавления нового мессенджера: создать класс → добавить в Union.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field


class BaseMessengerConfig(BaseModel):
    """Поля, общие для всех мессенджеров (доступны до isinstance-сужения)."""
    id: str
    kind: str                       # перекрыто Literal в подклассах
    tag: Optional[str] = None       # переопределение тега-префикса


class TelegramMessengerConfig(BaseMessengerConfig):
    kind: Literal["telegram"]
    token: str


MessengerConfig = Annotated[
    Union[TelegramMessengerConfig],  # расширять Union при добавлении мессенджеров
    Field(discriminator="kind"),
]
