"""Доменные модели (§4).

Одной модели ``Message`` хватает на оба направления: при зеркалировании в другие
каналы у неё просто другой ``source``. Адаптер сам решает, как отрисовать
``sender + text`` (мессенджер) или превратить в плоскую строку (LoRa).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from typing import Optional


@dataclass(frozen=True)
class ChannelRef:
    transport_id: str
    channel: str  # opaque id эндпоинта; топик — забота адаптера


def messenger_channel(chat: str, topic: Optional[str]) -> str:
    """Канонический opaque ``ChannelRef.channel`` для мессенджер-эндпоинта.

    Единый контракт для RoomRegistry (ядро) и мессенджер-адаптера (транспорт) —
    обе стороны кодируют (chat, topic) одинаково, иначе RX не сматчится с комнатой.
    """
    return f"{chat}#{topic}" if topic else chat


@dataclass(frozen=True)
class Identity:
    display_name: str
    transport_uid: str


@dataclass(frozen=True)
class Message:
    id: str  # стабильный id транспорта (для dedup)
    source: ChannelRef
    sender: Identity
    text: str
    # Время источника, если извлекается. Для LoRa часто None — не выдумываем.
    timestamp: Optional[dt.datetime] = None
    origin_tag: Optional[str] = None  # loop-guard (только LoRa-путь)


class DeliveryStatus(Enum):
    PENDING = "pending"  # принято в commit-очередь
    TRANSMITTING = "transmitting"  # взято воркером, отдано узлу
    SENT = "sent"  # commit подтверждён узлом (терминальный успех)
    REJECTED = "rejected"  # admission отклонил (см. RejectReason)
    FAILED = "failed"  # нет commit в таймаут / ошибка узла
    UNKNOWN = "unknown"  # рестарт во время TRANSMITTING — ушло ли, неизвестно (§11.1)


class RejectReason(Enum):
    TOO_LONG = "too_long"  # префикс+текст > max_text_bytes (НЕ усекаем)
    RATE_LIMIT = "rate_limit"  # эфир перегружен (token-bucket / очередь полна)
    TTL_EXPIRED = "ttl_expired"  # протухло в очереди до отправки


@dataclass(frozen=True)
class RateSpec:
    msgs_per_window: int
    window_seconds: float
    burst: int = 1


@dataclass(frozen=True)
class Capabilities:
    max_text_bytes: int
    egress_rate: Optional[RateSpec] = None
    supports_status_feedback: bool = False  # умеет показать статус (реакция)
    emits_tx_done: bool = False  # узел отдаёт TX-done (commit); у MeshCore False (§5.1)


@dataclass(frozen=True)
class SendResult:
    """Результат ``Transport.send`` (§5, §5.1/R4).

    ``ok`` — commit достигнут (MSG_OK для каналов / ACK для room_server).
    ``busy`` — очередь узла полна (``TABLE_FULL``): не FAILED, а повтор позже.
    """

    ok: bool
    busy: bool = False
    detail: str = ""

    @classmethod
    def success(cls) -> SendResult:
        return cls(ok=True)

    @classmethod
    def failure(cls, detail: str = "") -> SendResult:
        return cls(ok=False, detail=detail)

    @classmethod
    def overloaded(cls, detail: str = "") -> SendResult:
        return cls(ok=False, busy=True, detail=detail)


@dataclass(frozen=True)
class LabelFormat:
    """Параметры сборки префикса ``[тип:ник]`` (§4, конфиг policies.label)."""

    include_type: bool = True
    max_nick_bytes: int = 24


@dataclass
class Room:
    """Логическая комната: один LoRa-эндпоинт ↔ N подписчиков-мессенджеров (§12)."""

    lora_endpoint: str  # ключ из node.endpoints → ChannelRef.channel
    writable_messenger_count: int  # сколько мессенджеров ПИШУТ в комнату (для AD-10)
    node_id: str = ""  # id LoRa-ноды (lora[].id)
