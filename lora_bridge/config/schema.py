"""Pydantic-схема конфига (§12). Соответствует config.example.yaml.

Эндпоинты — discriminated union по полю ``type`` (public/private/room_server),
чтобы конфиг был самодокументируемым (никакого «есть pubkey ⇒ room server», §5.1).
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field

# --- LoRa: соединение ---------------------------------------------------------


class UsbConnection(BaseModel):
    type: Literal["usb"]
    device_id: str                      # VID:PID, напр. "0333:0303"


class SerialConnection(BaseModel):
    type: Literal["serial"]
    port: str                           # /dev/ttyUSB0 / COM3


class TcpConnection(BaseModel):
    type: Literal["tcp"]
    host: str
    port: int


class BleConnection(BaseModel):
    type: Literal["ble"]
    address: str


Connection = Annotated[
    Union[UsbConnection, SerialConnection, TcpConnection, BleConnection],
    Field(discriminator="type"),
]

# --- LoRa: эндпоинты (три типа) -----------------------------------------------


class PublicEndpoint(BaseModel):
    type: Literal["public"]             # Public channel (общий PSK)


class PrivateEndpoint(BaseModel):
    type: Literal["private"]            # Channel со своим secret
    secret: str


class RoomServerEndpoint(BaseModel):
    type: Literal["room_server"]        # Room Server: pubkey + guest-пароль
    pubkey: str
    password: Optional[str] = None      # пусто → read-only (постинг недоступен)


Endpoint = Annotated[
    Union[PublicEndpoint, PrivateEndpoint, RoomServerEndpoint],
    Field(discriminator="type"),
]


# --- Политики ноды (радио-специфичны → живут внутри ноды) ---------------------


class EgressRate(BaseModel):
    msgs_per_window: int
    window_seconds: float


class ReconnectBackoff(BaseModel):
    base: float = 2
    max: float = 60
    jitter: bool = True


class LabelPolicy(BaseModel):
    include_type: Literal["auto", "always", "never"] = "auto"
    format: str = "[{type}:{nick}] "
    max_nick_bytes: int = 24
    on_oversize: Literal["reject"] = "reject"   # НЕ truncate (AD-11)


class NodePolicies(BaseModel):
    egress_rate: EgressRate
    queue_ttl_seconds: float = 45
    commit_timeout_seconds: float = 30
    reconnect_backoff: ReconnectBackoff = ReconnectBackoff()
    dedup_ttl_seconds: float = 300
    drop_notice_window_seconds: float = 60
    label: LabelPolicy = LabelPolicy()


# --- LoRa-ноды ----------------------------------------------------------------
# Каждая нода имеет ЯВНЫЙ `type` (прошивка/протокол). Сейчас поддержан `meshcore`;
# `type` — точка расширения под discriminated union (будущий MeshtasticNode),
# фундамент для LoRa↔LoRa-мостинга.


class MeshCoreNode(BaseModel):
    id: str                             # идентификатор ноды (ссылка из rooms[].lora.node)
    type: Literal["meshcore"] = "meshcore"
    connection: Connection
    endpoints: dict[str, Endpoint]      # MAP: имя эндпоинта → конфиг
    policies: NodePolicies


LoraNode = MeshCoreNode                 # TODO: Union[MeshCoreNode, MeshtasticNode] по `type`


# --- Мессенджеры --------------------------------------------------------------


class MessengerConfig(BaseModel):
    id: str
    kind: str                           # telegram | … → тег по умолчанию
    token: str
    tag: Optional[str] = None           # переопределение тега префикса


# --- Комнаты ------------------------------------------------------------------


class Subscriber(BaseModel):
    transport: str                      # messengers[].id
    chat: str
    topic: Optional[str] = None         # None → General (и только он)


class LoraRef(BaseModel):
    node: str                           # lora[].id
    endpoint: str                       # ключ из node.endpoints


class RoomConfig(BaseModel):
    lora: LoraRef                       # node-qualified (имена эндпоинтов уникальны лишь в ноде)
    subscribers: list[Subscriber]


# --- Корень -------------------------------------------------------------------


class AppConfig(BaseModel):
    lora: list[LoraNode]                # несколько физических нод
    messengers: list[MessengerConfig]
    rooms: list[RoomConfig]
