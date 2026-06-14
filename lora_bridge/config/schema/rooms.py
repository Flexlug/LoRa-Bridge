"""Комнаты — группы, где сообщения зеркалятся между участниками.

Инвариант формы (§12.1): либо «1 LoRa + N мессенджеров», либо «2 LoRa + 0 мессенджеров».
Смешивать нельзя — семантика префикса/статусов стала бы неоднозначной.
"""
from __future__ import annotations

from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, model_validator


class LoraRef(BaseModel):
    node: str       # lora[].id
    endpoint: str   # ключ из node.endpoints


class MessengerSubscriber(BaseModel):
    model_config = ConfigDict(extra="forbid")   # чёткая дискриминация union'а
    transport: str                  # messengers[].id
    chat: str
    topic: Optional[str] = None     # None → General (и только он)


class LoraSubscriber(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lora: LoraRef                   # LoRa-эндпоинт как подписчик (LoRa↔LoRa)


Subscriber = Union[MessengerSubscriber, LoraSubscriber]


class RoomConfig(BaseModel):
    lora: LoraRef                   # первичный LoRa-эндпоинт комнаты
    subscribers: list[Subscriber]

    @model_validator(mode="after")
    def enforce_shape(self) -> RoomConfig:
        loras = [s for s in self.subscribers if isinstance(s, LoraSubscriber)]
        msgs = [s for s in self.subscribers if isinstance(s, MessengerSubscriber)]
        if loras:
            if len(loras) != 1 or msgs:
                raise ValueError(
                    "LoRa↔LoRa-комната допускает ровно один LoRa-подписчик "
                    "и НИ одного мессенджера (итого 2 LoRa-эндпоинта)"
                )
            if loras[0].lora == self.lora:
                raise ValueError("LoRa-подписчик совпадает с первичным эндпоинтом (self-loop)")
        elif not msgs:
            raise ValueError(
                "у комнаты нет подписчиков: нужен ≥1 мессенджер или один LoRa-эндпоинт"
            )
        return self
