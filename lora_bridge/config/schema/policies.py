"""Политики LoRa-узла — рейт-лимит, TTL, поведение префикса.

Эти параметры радио-специфичны (зависят от duty cycle региона, скорости SF),
поэтому живут на уровне ноды, а не глобально.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EgressRate(BaseModel):
    """Token-bucket rate-limit на исходящие сообщения в LoRa.

    Защищает эфир от перегруза и соблюдает duty cycle. См. §7 архитектуры.
    """

    msgs_per_window: int = Field(
        description="Сколько сообщений можно отправить за одно окно."
    )
    window_seconds: float = Field(description="Длина окна rate-limit'а в секундах.")


class ReconnectBackoff(BaseModel):
    """Параметры экспоненциального переподключения к узлу."""

    base: float = Field(
        default=2, description="Базовый делей в секундах для первой попытки."
    )
    max: float = Field(default=60, description="Максимальный делей между попытками.")
    jitter: bool = Field(
        default=True,
        description="Добавлять случайный джиттер для распределения нагрузки.",
    )


class LabelPolicy(BaseModel):
    """Поведение префикса ``[тип:ник]`` при выгрузке в LoRa (AD-10, AD-11)."""

    include_type: Literal["auto", "always", "never"] = Field(
        default="auto",
        description=(
            "Когда добавлять тег типа транспорта. ``auto`` — только если в комнату "
            "пишут больше одного мессенджера; ``always`` — всегда; ``never`` — никогда."
        ),
    )
    max_nick_bytes: int = Field(
        default=24,
        description=(
            "Лимит на длину ника в байтах. При превышении ник усекается — "
            "сам текст не трогаем никогда (AD-11)."
        ),
    )
    on_oversize: Literal["reject"] = Field(
        default="reject",
        description=(
            "Что делать, если префикс+текст не влезают в ``max_text_bytes``. "
            "Только ``reject`` — текст НЕ усекается (AD-11)."
        ),
    )


class NodePolicies(BaseModel):
    """Набор радио-политик ноды."""

    egress_rate: EgressRate = Field(
        description="Token-bucket rate-limit на исходящие сообщения."
    )
    queue_ttl_seconds: float = Field(
        default=45,
        description="Admission TTL очереди commit'ов. Протухшее уходит в REJECTED.",
    )
    commit_timeout_seconds: float = Field(
        default=30,
        description=(
            "Таймаут commit'а: ACK для room_server либо ``MSG_OK`` для public/private."
        ),
    )
    reconnect_backoff: ReconnectBackoff = Field(
        default_factory=ReconnectBackoff,
        description="Параметры reconnect-стратегии при потере связи с узлом.",
    )
    dedup_ttl_seconds: float = Field(
        default=300, description="TTL ключей дедупликации входящих сообщений."
    )
    drop_notice_window_seconds: float = Field(
        default=60,
        description=(
            "Окно агрегации уведомлений о дропах: одинаковые «дропнули» в течение "
            "окна склеиваются в одно сообщение в обратку."
        ),
    )
    label: LabelPolicy = Field(
        default_factory=LabelPolicy,
        description="Поведение префикса при выгрузке сообщений в LoRa.",
    )
