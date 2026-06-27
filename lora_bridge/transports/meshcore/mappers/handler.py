"""Супертип эндпоинта MeshCore и маршрутизация RX (§5.1, AD-5).

Транспорту безразличен тип эндпоинта: он держит набор ``EndpointHandler`` и
говорит каждому «подготовь себя» (``resolve``), «отправь» (``send``), «это твоё
событие? тогда вот Message» (``try_rx``). Вся type-specific логика живёт в
конкретных хэндлерах (public/private/room_server), а единственный ``match`` по
типу — в фабрике ``init_endpoint_handler`` (см. ``__init__``).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Iterable

from meshcore import EventType as McEventType, MeshCore

from ....domain.models import Message

log = logging.getLogger(__name__)

EV_CHANNEL_MSG = McEventType.CHANNEL_MSG_RECV
EV_CONTACT_MSG = McEventType.CONTACT_MSG_RECV


@dataclass(frozen=True)
class ResolveContext:
    """Всё, что нужно хэндлеру для подготовки к старту на конкретном радио.

    Создаётся транспортом перед циклом ``resolve``; ``channel_names`` — имена
    всех channel-эндпоинтов узла (для вытеснения чужих слотов при переполнении).
    """

    mc: MeshCore
    node_id: str
    channel_names: frozenset[str]
    override_oldest_channel: bool
    override_oldest_contact: bool


class EndpointHandler(ABC):
    """Поведение одного эндпоинта: подготовка, отправка, разбор входящего RX."""

    name: str
    rx_event_type: ClassVar[McEventType]  # какое RX-событие потребляет этот тип

    @abstractmethod
    async def resolve(self, ctx: ResolveContext) -> None:
        """Подготовить эндпоинт к работе на устройстве (слот канала / контакт + login)."""

    @abstractmethod
    async def send(self, mc: MeshCore, text: str, node_id: str) -> Any:
        """Отправить текст; вернуть сырой ответ устройства (классификация — в транспорте)."""

    @abstractmethod
    def try_rx(self, payload: dict[str, Any], node_id: str) -> Message | None:
        """Если событие адресовано этому эндпоинту — вернуть Message, иначе None."""

    @abstractmethod
    def rx_key(self) -> str:
        """Ключ маршрутизации RX (channel_idx / pubkey prefix) — для диагностики дропов."""


def route_rx(handlers: Iterable[EndpointHandler], event: Any, node_id: str) -> Message | None:
    """Отдать RX-событие первому хэндлеру нужного типа, который его опознает."""
    payload = event.payload if isinstance(event.payload, dict) else {}
    candidates = [h for h in handlers if h.rx_event_type == event.type]
    for handler in candidates:
        msg = handler.try_rx(payload, node_id)
        if msg is not None:
            return msg
    if candidates:
        log.warning(
            "нода '%s': %s не совпал ни с одним эндпоинтом — дроп (payload=%s, эндпоинты=%s)",
            node_id, event.type, payload,
            {h.name: h.rx_key() for h in candidates},
        )
    return None
