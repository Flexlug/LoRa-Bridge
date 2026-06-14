from __future__ import annotations

from typing import assert_never

from pydantic import BaseModel, model_validator

from .messengers import MessengerConfig
from .nodes import LoraNode
from .rooms import LoraRef, LoraSubscriber, MessengerSubscriber, RoomConfig


def validate_lora_ref(ref: LoraRef, where: str, node_eps: dict[str, set[str]]) -> None:
    if ref.node not in node_eps:
        raise ValueError(f"{where}: неизвестная LoRa-нода '{ref.node}'")
    if ref.endpoint not in node_eps[ref.node]:
        raise ValueError(f"{where}: у ноды '{ref.node}' нет эндпоинта '{ref.endpoint}'")


class AppConfig(BaseModel):
    lora: list[LoraNode]
    messengers: list[MessengerConfig]
    rooms: list[RoomConfig]

    @model_validator(mode="after")
    def validate_unique_ids(self) -> AppConfig:
        node_ids = [n.id for n in self.lora]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("дублирующиеся id LoRa-нод")
        msg_ids = [m.id for m in self.messengers]
        if len(set(msg_ids)) != len(msg_ids):
            raise ValueError("дублирующиеся id мессенджеров")
        return self

    @model_validator(mode="after")
    def validate_room_refs(self) -> AppConfig:
        node_eps = {n.id: set(n.endpoints) for n in self.lora}
        msg_ids = {m.id for m in self.messengers}
        for i, room in enumerate(self.rooms):
            validate_lora_ref(room.lora, f"rooms[{i}].lora", node_eps)
            for s in room.subscribers:
                match s:
                    case LoraSubscriber():
                        validate_lora_ref(s.lora, f"rooms[{i}].subscribers.lora", node_eps)
                    case MessengerSubscriber():
                        if s.transport not in msg_ids:
                            raise ValueError(
                                f"rooms[{i}].subscribers: неизвестный мессенджер '{s.transport}'"
                            )
                    case _ as unreachable:
                        assert_never(unreachable)
        return self
