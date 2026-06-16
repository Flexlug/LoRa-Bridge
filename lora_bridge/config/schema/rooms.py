"""Комнаты — группы каналов, между которыми зеркалятся сообщения.

Инвариант формы (§12.1): либо «1 LoRa + N мессенджеров», либо «2 LoRa + 0 мессенджеров».
Смешивать нельзя — семантика префикса/статусов стала бы неоднозначной.
"""

from __future__ import annotations

from typing import Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ids import EndpointName, MessengerId, NodeId


class LoraRef(BaseModel):
    """Ссылка на конкретный LoRa-эндпоинт (нода + имя эндпоинта)."""

    node: NodeId = Field(description="Id ноды из ``lora[].id``.")
    endpoint: EndpointName = Field(
        description="Ключ эндпоинта из ``lora[].endpoints``."
    )


class MessengerSubscriber(BaseModel):
    """Подписчик-чат мессенджера."""

    model_config = ConfigDict(extra="forbid")  # чёткая дискриминация union'а

    transport: MessengerId = Field(
        description="Id мессенджера из ``messengers[].id``."
    )
    chat: str = Field(
        description=(
            'Id чата. Для Telegram — chat_id, например "-1001234567890" '
            "(узнаётся через @userinfobot или getUpdates)."
        )
    )
    topic: Optional[str] = Field(
        default=None,
        description=(
            "Тема (thread) внутри чата. Если опущена — работаем только с General. "
            "Если указана — работаем только с этой темой."
        ),
    )


class LoraSubscriber(BaseModel):
    """LoRa-эндпоинт как подписчик комнаты (LoRa↔LoRa relay)."""

    model_config = ConfigDict(extra="forbid")

    lora: LoraRef = Field(description="LoRa-эндпоинт-получатель для рилея.")


Subscriber = Union[MessengerSubscriber, LoraSubscriber]
"""Подписчик комнаты — либо чат мессенджера, либо peer LoRa-эндпоинт.

Smart union без явного дискриминатора: pydantic выбирает форму по набору полей
(``transport``/``chat`` → MessengerSubscriber; ``lora`` → LoraSubscriber).
"""


class RoomConfig(BaseModel):
    """Логическая комната — связывает один LoRa-эндпоинт с подписчиками.

    Допустимые формы:

    * ``1 LoRa + N мессенджеров`` — сообщения из LoRa зеркалятся подписчикам,
      из мессенджеров уходят в эфир.
    * ``2 LoRa + 0 мессенджеров`` — рилей между двумя радиосетями.

    Смешанная форма (несколько LoRa-подписчиков ИЛИ LoRa + мессенджер) запрещена.
    """

    lora: LoraRef = Field(description="Первичный LoRa-эндпоинт комнаты.")
    subscribers: list[Subscriber] = Field(
        description=(
            "Подписчики, между которыми зеркалятся сообщения. Допустимые формы: "
            "≥1 мессенджер либо ровно один LoRa-подписчик (для LoRa↔LoRa)."
        )
    )

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
