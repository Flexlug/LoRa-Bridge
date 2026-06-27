"""Private-канал MeshCore: одна точка входа — ``PrivateChannelHandler``.

PSK задаётся явно (raw hex из MeshCore-приложения), поэтому ``secret_bytes`` =
bytes.fromhex(secret). Вся общая логика каналов — в ``channel_util``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from meshcore import MeshCore

from . import channel_util
from .handler import EV_CHANNEL_MSG, EndpointHandler, ResolveContext
from ....domain.models import Message


@dataclass
class PrivateChannelHandler(EndpointHandler):
    name: str
    channel_name: str
    secret: str  # raw hex PSK из MeshCore-приложения
    channel_index: int | None = None  # резолвится в resolve()
    rx_event_type: ClassVar = EV_CHANNEL_MSG

    async def resolve(self, ctx: ResolveContext) -> None:
        self.channel_index = await channel_util.resolve_channel(
            ctx.mc,
            channel_name=self.channel_name,
            secret_bytes=bytes.fromhex(self.secret),
            node_id=ctx.node_id,
            configured_channel_names=ctx.channel_names,
            override_oldest=ctx.override_oldest_channel,
        )

    async def send(self, mc: MeshCore, text: str, node_id: str) -> Any:
        return await channel_util.send_channel(mc, self.channel_index, text, node_id)

    def try_rx(self, payload: dict[str, Any], node_id: str) -> Message | None:
        if payload.get("channel_idx", -1) != self.channel_index:
            return None
        return channel_util.channel_to_message(payload, self.name, node_id)

    def rx_key(self) -> str:
        return f"channel_idx={self.channel_index}"
