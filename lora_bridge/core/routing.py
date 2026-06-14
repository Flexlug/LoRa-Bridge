"""Рантайм-комнаты и реестр маршрутизации (§12, §12.1).

Комната — набор участников (LoRa-эндпоинты и/или мессенджеры). Реестр индексирует
участников по ``ChannelRef`` источника, чтобы по входящему сообщению найти комнату
и остальных участников.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

from ..domain.models import ChannelRef, messenger_channel


@dataclass(frozen=True)
class LoraMember:
    node_id: str
    endpoint: str

    @property
    def ref(self) -> ChannelRef:
        return ChannelRef(self.node_id, self.endpoint)


@dataclass(frozen=True)
class MessengerMember:
    transport_id: str
    chat: str
    topic: Optional[str] = None

    @property
    def ref(self) -> ChannelRef:
        return ChannelRef(self.transport_id, messenger_channel(self.chat, self.topic))


Member = Union[LoraMember, MessengerMember]


@dataclass(frozen=True)
class RoomRoute:
    members: tuple[Member, ...]

    @property
    def writable_messenger_count(self) -> int:
        return sum(1 for m in self.members if isinstance(m, MessengerMember))

    def others(self, source: ChannelRef) -> list[Member]:
        return [m for m in self.members if m.ref != source]

    def messenger_members(self) -> list[MessengerMember]:
        return [m for m in self.members if isinstance(m, MessengerMember)]


class RoomRegistry:
    """Маппинг ``ChannelRef`` участника → его комната (§12)."""

    def __init__(self, routes: list[RoomRoute]) -> None:
        self._by_ref: dict[ChannelRef, RoomRoute] = {}
        for route in routes:
            for m in route.members:
                # один эндпоинт может состоять максимум в одной комнате (инвариант топологии)
                if m.ref in self._by_ref:
                    raise ValueError(f"эндпоинт {m.ref} состоит более чем в одной комнате")
                self._by_ref[m.ref] = route

    def for_source(self, source: ChannelRef) -> Optional[RoomRoute]:
        return self._by_ref.get(source)
