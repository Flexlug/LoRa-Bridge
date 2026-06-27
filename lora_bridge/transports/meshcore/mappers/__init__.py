"""Мапперы MeshCore-эндпоинтов по типам (§5.1, AD-5).

Каждый тип эндпоинта живёт в своём модуле с единственной точкой входа —
хэндлером (``public``/``private``/``room_server``); общая логика каналов — в
``channel_util``. Здесь — фабрика из конфига, единственное место с ``match`` по
типу: дальше транспорт работает с ``EndpointHandler`` не зная конкретного типа.
"""

from __future__ import annotations

from typing import Iterable, assert_never

from ....config.schema import (
    Endpoint,
    PrivateEndpoint,
    PublicEndpoint,
    RoomServerEndpoint,
)
from .handler import EndpointHandler, ResolveContext, route_rx
from .private import PrivateChannelHandler
from .public import PublicChannelHandler
from .room_server import RoomServerHandler


def init_endpoint_handler(name: str, ep: Endpoint) -> EndpointHandler:
    """Config-эндпоинт → хэндлер. Единственный ``match`` по типу во всём пакете."""
    match ep:
        case PublicEndpoint():
            return PublicChannelHandler(name=name, channel_name=ep.channel_name)
        case PrivateEndpoint():
            return PrivateChannelHandler(
                name=name, channel_name=ep.channel_name, secret=ep.secret
            )
        case RoomServerEndpoint():
            return RoomServerHandler(name=name, pubkey=ep.pubkey, password=ep.password)
        case _ as unreachable:
            assert_never(unreachable)


def collect_channel_names(handlers: Iterable[EndpointHandler]) -> frozenset[str]:
    """Имена всех channel-эндпоинтов узла (для вытеснения чужих слотов).

    Знание о том, какие хэндлеры — каналы, держится здесь, рядом с фабрикой,
    чтобы транспорт оставался type-agnostic.
    """
    return frozenset(
        h.channel_name
        for h in handlers
        if isinstance(h, (PublicChannelHandler, PrivateChannelHandler))
    )


__all__ = [
    "EndpointHandler",
    "ResolveContext",
    "PublicChannelHandler",
    "PrivateChannelHandler",
    "RoomServerHandler",
    "init_endpoint_handler",
    "collect_channel_names",
    "route_rx",
]
