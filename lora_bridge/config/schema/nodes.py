"""LoRa-ноды (физическое радиоустройство + прошивка).

Каждая нода имеет явный ``type`` (прошивка/протокол). Сейчас поддержан ``meshcore``;
``type`` — точка расширения под discriminated union (будущий MeshtasticNode).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .connections import Connection
from .endpoints import Endpoint
from .policies import NodePolicies


class MeshCoreNode(BaseModel):
    id: str  # ссылка из rooms[].lora.node
    type: Literal["meshcore"] = "meshcore"
    connection: Connection
    endpoints: dict[str, Endpoint]  # имя эндпоинта → конфиг
    policies: NodePolicies


LoraNode = MeshCoreNode  # TODO: Union[MeshCoreNode, MeshtasticNode] по `type`
