"""Public-канал MeshCore: одна точка входа — ``PublicChannelHandler``.

PSK выводится из имени канала (sha256(name)[:16]) внутри meshcore, поэтому
``secret_bytes`` = None. Вся общая логика каналов — в ``channel_util``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from . import channel_util
from .handler import EV_CHANNEL_MSG, EndpointHandler, ResolveContext
from ....domain.models import Message


@dataclass
class PublicChannelHandler(EndpointHandler):
    name: str
    channel_name: str
    channel_index: int | None = None  # резолвится в resolve()
    rx_event_type: ClassVar = EV_CHANNEL_MSG

    async def resolve(self, ctx: ResolveContext) -> None:
        self.channel_index = await channel_util.resolve_channel(
            ctx.mc,
            channel_name=self.channel_name,
            secret_bytes=None,  # PSK = sha256(name)[:16] внутри meshcore
            node_id=ctx.node_id,
            configured_channel_names=ctx.channel_names,
            override_oldest=ctx.override_oldest_channel,
        )

    async def send(self, mc: Any, text: str, node_id: str) -> Any:
        return await channel_util.send_channel(mc, self.channel_index, text, node_id)

    def try_rx(self, payload: dict[str, Any], node_id: str) -> Message | None:
        if payload.get("channel_idx", -1) != self.channel_index:
            return None
        return channel_util.channel_to_message(payload, self.name, node_id)

    def rx_key(self) -> str:
        return f"channel_idx={self.channel_index}"
