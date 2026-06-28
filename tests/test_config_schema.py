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
    TelegramCommandsConfig,
    TelegramMessengerConfig,
)

_POLICIES = {"egress_rate": {"msgs_per_window": 6, "window_seconds": 60}}
_TCP = {"type": "tcp", "host": "h", "port": 1}
_PSK = "00112233445566778899aabbccddeeff"  # валидный PSK: 32 hex-символа = 16 байт


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


@pytest.mark.parametrize(
    "conn",
    [
        {"type": "bluetooth", "address": "x"},  # неизвестный тег дискриминатора
        {"type": "tcp", "port": 5050},          # tcp без host
        {"type": "tcp", "host": "h"},           # tcp без port
    ],
    ids=["unknown_type", "tcp_missing_host", "tcp_missing_port"],
)
def test_invalid_connection_rejected(conn):
    with pytest.raises(ValidationError):
        MeshCoreNode.model_validate(_node(conn))


# ---------------------------------------------------------------------------
# Endpoint — happy paths для всех 3 типов
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ep",
    [
        {"type": "public", "channel_name": "General"},
        {"type": "private", "channel_name": "Ops", "secret": _PSK},
        {"type": "room_server", "pubkey": "abcdef123"},
        {"type": "room_server", "pubkey": "abcdef123", "password": "pw"},
    ],
)
def test_all_endpoint_types_valid(ep):
    MeshCoreNode.model_validate(_node(_TCP, {"ch": ep}))


# ---------------------------------------------------------------------------
# Endpoint — невалидные входы
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ep",
    [
        {"type": "multicast"},                          # неизвестный тег
        {"type": "public"},                             # public без channel_name
        {"type": "private", "secret": "s"},             # private без channel_name
        {"type": "private", "channel_name": "Ops"},     # private без secret
        {"type": "room_server"},                        # room_server без pubkey
    ],
    ids=[
        "unknown_type",
        "public_missing_channel_name",
        "private_missing_channel_name",
        "private_missing_secret",
        "room_server_missing_pubkey",
    ],
)
def test_invalid_endpoint_rejected(ep):
    with pytest.raises(ValidationError):
        MeshCoreNode.model_validate(_node(_TCP, {"ch": ep}))


# ---------------------------------------------------------------------------
# Endpoint — нормализация и валидаторы (LoRa-Bridge-305 / 3o9)
# ---------------------------------------------------------------------------


def test_room_server_pubkey_lowercased():
    # Либа отдаёт pubkey_prefix в нижнем регистре, сравнение регистрозависимое:
    # заглавный pubkey из конфига должен нормализоваться, иначе тихий дроп RX.
    node = MeshCoreNode.model_validate(
        _node(_TCP, {"ch": {"type": "room_server", "pubkey": "ABCDEF123456"}})
    )
    assert node.endpoints["ch"].pubkey == "abcdef123456"


def test_private_endpoint_valid_secret_accepted():
    node = MeshCoreNode.model_validate(
        _node(_TCP, {"ch": {"type": "private", "channel_name": "Ops", "secret": _PSK}})
    )
    assert node.endpoints["ch"].secret == _PSK


@pytest.mark.parametrize(
    "secret",
    [
        "00112233445566778899aabbccddee",    # 30 символов — короткий
        "00112233445566778899aabbccddeeff00",  # 34 символа — длинный
        "00112233445566778899aabbccddeezz",  # 32 символа, но не hex
    ],
)
def test_private_endpoint_invalid_secret_rejected(secret):
    with pytest.raises(ValidationError):
        MeshCoreNode.model_validate(
            _node(_TCP, {"ch": {"type": "private", "channel_name": "Ops", "secret": secret}})
        )


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


# ---------------------------------------------------------------------------
# TelegramCommandsConfig
# ---------------------------------------------------------------------------


def test_telegram_commands_optional() -> None:
    cfg = TelegramMessengerConfig(id="tg", kind="telegram", token="tok")
    assert cfg.commands is None


def test_telegram_commands_with_block() -> None:
    cfg = TelegramMessengerConfig(
        id="tg", kind="telegram", token="tok",
        commands=TelegramCommandsConfig(owner_id=123),
    )
    assert cfg.commands is not None
    assert cfg.commands.owner_id == 123
    assert cfg.commands.alias_max_chars == 16


def test_telegram_commands_alias_max_chars_custom() -> None:
    cfg = TelegramMessengerConfig(
        id="tg", kind="telegram", token="tok",
        commands=TelegramCommandsConfig(owner_id=1, alias_max_chars=8),
    )
    assert cfg.commands is not None
    assert cfg.commands.alias_max_chars == 8
