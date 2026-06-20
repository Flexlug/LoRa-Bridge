"""LoRa-ноды (физическое радиоустройство + прошивка).

Каждая нода имеет явный ``type`` (прошивка/протокол). Сейчас поддержан ``meshcore``;
``type`` — точка расширения под discriminated union (будущий MeshtasticNode).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .connections import Connection
from .endpoints import Endpoint
from .ids import EndpointName, NodeId
from .policies import NodePolicies


class MeshCoreNode(BaseModel):
    """Конфиг одной физической LoRa-ноды с прошивкой MeshCore.

    Соответствует элементу массива ``lora:`` в YAML.
    """

    id: NodeId = Field(
        description=(
            "Уникальный id ноды. На него ссылается ``rooms[].lora.node`` (и "
            "``rooms[].subscribers[].lora.node`` при LoRa↔LoRa)."
        )
    )
    type: Literal["meshcore"] = Field(
        default="meshcore",
        description=(
            "Тип прошивки/протокола ноды. Точка расширения под другие прошивки."
        ),
    )
    connection: Connection = Field(description="Способ физического подключения к узлу.")
    endpoints: dict[EndpointName, Endpoint] = Field(
        description=(
            "Карта эндпоинтов ноды: имя → конфиг канала. На имя ссылается "
            "``rooms[].lora.endpoint``. Одна нода может обслуживать несколько каналов."
        )
    )
    policies: NodePolicies = Field(
        description="Радио-специфичные политики ноды (rate-limit, TTL, label)."
    )
    log_raw_rx: bool = Field(
        default=False,
        description="Логировать RX_LOG_DATA (сырые пакеты эфира). По умолчанию выключено — слишком шумно.",
    )


LoraNode = MeshCoreNode  # TODO: Union[MeshCoreNode, MeshtasticNode] по `type`
