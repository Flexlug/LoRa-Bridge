"""Мапперы MeshCore-эндпоинтов по типам (§5.1, AD-5).

Каждый тип эндпоинта (public/private канал, room server) живёт в своём модуле:
там его состояние (state), резолв на устройстве, отправка и нормализация RX в
доменный ``Message``. Здесь — общий union состояний и фабрика из конфига.
Транспорт держит тонкую ``match``-диспетчеризацию поверх этих мапперов.
"""

from __future__ import annotations

from typing import assert_never

from ....config.schema import (
    PrivateEndpoint,
    PublicEndpoint,
    RoomServerEndpoint,
)
from . import channel, room_server
from .channel import PrivateEndpointState, PublicEndpointState
from .room_server import RoomServerEndpointState

EndpointState = PublicEndpointState | PrivateEndpointState | RoomServerEndpointState


def init_endpoint_state(
    name: str, ep: PublicEndpoint | PrivateEndpoint | RoomServerEndpoint
) -> EndpointState:
    match ep:
        case PublicEndpoint():
            return PublicEndpointState(name=name, channel_name=ep.channel_name)
        case PrivateEndpoint():
            return PrivateEndpointState(name=name, channel_name=ep.channel_name, secret=ep.secret)
        case RoomServerEndpoint():
            return RoomServerEndpointState(name=name, pubkey=ep.pubkey, password=ep.password)
        case _ as unreachable:
            assert_never(unreachable)


__all__ = [
    "EndpointState",
    "PublicEndpointState",
    "PrivateEndpointState",
    "RoomServerEndpointState",
    "channel",
    "room_server",
    "init_endpoint_state",
]
