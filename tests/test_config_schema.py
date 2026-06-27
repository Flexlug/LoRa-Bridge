"""Тесты типовой валидации отдельных схем конфига.

Проверяем discriminated union'ы, обязательные поля и ограничения
на уровне отдельных моделей — без сборки полного AppConfig.
"""

from typing import get_args

import pytest
from pydantic import TypeAdapter, ValidationError

from lora_bridge.config.schema import (
    Connection,
    ConnectionBase,
    Endpoint,
    EndpointBase,
    MessengerConfig,
    MeshCoreNode,
    TelegramMessengerConfig,
)

_POLICIES = {"egress_rate": {"msgs_per_window": 6, "window_seconds": 60}}
_TCP = {"type": "tcp", "host": "h", "port": 1}


def _node(connection, endpoints=None):
    return {
        "id": "n1",
        "type": "meshcore",
        "connection": connection,
        "endpoints": endpoints or {"ch": {"type": "public", "channel_name": "General"}},
        "policies": _POLICIES,
    }


# ---------------------------------------------------------------------------
# Connection — happy paths для всех 4 типов
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "conn",
    [
        {"type": "tcp", "host": "192.168.1.1", "port": 5050},
        {"type": "serial", "port": "/dev/ttyUSB0"},
        {"type": "usb", "device_id": "0403:6015"},
        {"type": "ble", "address": "AA:BB:CC:DD:EE:FF"},
    ],
)
def test_all_connection_types_valid(conn):
    MeshCoreNode.model_validate(_node(conn))


def _assert_union_matches_subclasses(union, base):
    """Сверяет ветки дискриминированного union со списком наследников базы.

    Стережёт сценарий «добавил новый класс, но забыл дописать в union» —
    тогда падает здесь с понятным сообщением, а не тихо игнорируется при
    валидации конфига. ``union`` имеет вид ``Annotated[Union[...], Field(...)]``,
    поэтому ветки достаём двойным ``get_args``.
    """
    declared = set(get_args(get_args(union)[0]))
    subclasses = set(base.__subclasses__())
    assert declared == subclasses, (
        f"рассинхрон union и наследников {base.__name__}: "
        f"в union нет {subclasses - declared}, лишние в union {declared - subclasses}"
    )


def test_connection_union_is_exhaustive():
    _assert_union_matches_subclasses(Connection, ConnectionBase)


def test_endpoint_union_is_exhaustive():
    _assert_union_matches_subclasses(Endpoint, EndpointBase)


# ---------------------------------------------------------------------------
# Connection — невалидные входы
# ---------------------------------------------------------------------------


def test_unknown_connection_type_rejected():
    with pytest.raises(ValidationError):
        MeshCoreNode.model_validate(_node({"type": "bluetooth", "address": "x"}))


def test_tcp_missing_host_rejected():
    with pytest.raises(ValidationError):
        MeshCoreNode.model_validate(_node({"type": "tcp", "port": 5050}))


def test_tcp_missing_port_rejected():
    with pytest.raises(ValidationError):
        MeshCoreNode.model_validate(_node({"type": "tcp", "host": "h"}))


# ---------------------------------------------------------------------------
# Endpoint — happy paths для всех 3 типов
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ep",
    [
        {"type": "public", "channel_name": "General"},
        {"type": "private", "channel_name": "Ops", "secret": "my-psk"},
        {"type": "room_server", "pubkey": "abcdef123"},
        {"type": "room_server", "pubkey": "abcdef123", "password": "pw"},
    ],
)
def test_all_endpoint_types_valid(ep):
    MeshCoreNode.model_validate(_node(_TCP, {"ch": ep}))


# ---------------------------------------------------------------------------
# Endpoint — невалидные входы
# ---------------------------------------------------------------------------


def test_unknown_endpoint_type_rejected():
    with pytest.raises(ValidationError):
        MeshCoreNode.model_validate(_node(_TCP, {"ch": {"type": "multicast"}}))


def test_public_endpoint_missing_channel_name_rejected():
    with pytest.raises(ValidationError):
        MeshCoreNode.model_validate(_node(_TCP, {"ch": {"type": "public"}}))


def test_private_endpoint_missing_channel_name_rejected():
    with pytest.raises(ValidationError):
        MeshCoreNode.model_validate(_node(_TCP, {"ch": {"type": "private", "secret": "s"}}))


def test_private_endpoint_missing_secret_rejected():
    with pytest.raises(ValidationError):
        MeshCoreNode.model_validate(_node(_TCP, {"ch": {"type": "private", "channel_name": "Ops"}}))


def test_room_server_endpoint_missing_pubkey_rejected():
    with pytest.raises(ValidationError):
        MeshCoreNode.model_validate(_node(_TCP, {"ch": {"type": "room_server"}}))


# ---------------------------------------------------------------------------
# Messenger — happy paths
# ---------------------------------------------------------------------------


def test_telegram_minimal_valid():
    TelegramMessengerConfig.model_validate({"id": "tg", "kind": "telegram", "token": "abc"})


def test_telegram_with_tag_override_valid():
    cfg = TelegramMessengerConfig.model_validate(
        {"id": "tg", "kind": "telegram", "token": "abc", "tag": "ТГ"}
    )
    assert cfg.tag == "ТГ"


# ---------------------------------------------------------------------------
# Messenger — невалидные входы
# ---------------------------------------------------------------------------


def test_unknown_messenger_kind_rejected():
    ta = TypeAdapter(MessengerConfig)
    with pytest.raises(ValidationError):
        ta.validate_python({"id": "ms", "kind": "whatsapp", "token": "x"})


def test_telegram_missing_token_rejected():
    with pytest.raises(ValidationError):
        TelegramMessengerConfig.model_validate({"id": "tg", "kind": "telegram"})
