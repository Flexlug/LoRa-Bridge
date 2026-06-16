"""Тесты парсинга конфига из YAML-строк (§12).

В отличие от test_config_schema.py и test_config_crossref.py, здесь данные
проходят через yaml.safe_load() — проверяем реальный YAML-синтаксис и типы
(строки, числа, вложенность), а не Python-словари.
"""

import pytest
import yaml
from pydantic import ValidationError

from lora_bridge.config.schema import AppConfig

# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------


def parse(text: str) -> AppConfig:
    return AppConfig.model_validate(yaml.safe_load(text))


def bad(text: str):
    with pytest.raises(ValidationError):
        parse(text)


# ---------------------------------------------------------------------------
# Happy path — полный конфиг со всеми типами эндпоинтов и подключений
# ---------------------------------------------------------------------------

FULL = """
lora:
  - id: mc-1
    type: meshcore
    connection:
      type: usb
      device_id: "0403:6015"
    endpoints:
      general:
        type: public
        channel_name: "General"
      ops:
        type: private
        channel_name: "Ops"
        secret: "my-psk"
      srv:
        type: room_server
        pubkey: "aabbccddeeff"
        password: "hunter2"
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
messengers:
  - id: tg
    kind: telegram
    token: "123:ABC"
rooms:
  - lora:
      node: mc-1
      endpoint: general
    subscribers:
      - transport: tg
        chat: "-100"
"""


def test_full_config_parses():
    cfg = parse(FULL)
    node = cfg.lora[0]
    assert node.id == "mc-1"
    assert node.endpoints["general"].channel_name == "General"
    assert node.endpoints["ops"].channel_name == "Ops"
    assert node.endpoints["ops"].secret == "my-psk"
    assert node.endpoints["srv"].pubkey == "aabbccddeeff"
    assert node.endpoints["srv"].password == "hunter2"
    assert cfg.messengers[0].token == "123:ABC"


# ---------------------------------------------------------------------------
# Все типы подключений
# ---------------------------------------------------------------------------


def _cfg_with(*, connection: dict, endpoint: dict | None = None) -> str:
    """Собрать тестовый конфиг с заданными connection и endpoint, дампнуть в YAML.

    Тесты дальше передают Python-dict'ы, а не YAML-строки — так наглядней
    и не нужно следить за синтаксисом скобок.
    """
    cfg = {
        "lora": [
            {
                "id": "n",
                "type": "meshcore",
                "connection": connection,
                "endpoints": {
                    "ch": endpoint or {"type": "public", "channel_name": "General"},
                },
                "policies": {
                    "egress_rate": {"msgs_per_window": 6, "window_seconds": 60},
                },
            }
        ],
        "messengers": [{"id": "tg", "kind": "telegram", "token": "t"}],
        "rooms": [
            {
                "lora": {"node": "n", "endpoint": "ch"},
                "subscribers": [{"transport": "tg", "chat": "-1"}],
            }
        ],
    }
    return yaml.safe_dump(cfg, default_flow_style=False, sort_keys=False)


def test_connection_usb():
    cfg = parse(_cfg_with(connection={"type": "usb", "device_id": "0403:6015"}))
    assert cfg.lora[0].connection.device_id == "0403:6015"


def test_connection_serial():
    cfg = parse(_cfg_with(connection={"type": "serial", "port": "/dev/ttyUSB0"}))
    assert cfg.lora[0].connection.port == "/dev/ttyUSB0"


def test_connection_tcp():
    cfg = parse(_cfg_with(connection={"type": "tcp", "host": "192.168.1.1", "port": 5000}))
    conn = cfg.lora[0].connection
    assert conn.host == "192.168.1.1"
    assert conn.port == 5000


def test_connection_ble():
    cfg = parse(_cfg_with(connection={"type": "ble", "address": "AA:BB:CC:DD:EE:FF"}))
    assert cfg.lora[0].connection.address == "AA:BB:CC:DD:EE:FF"


# ---------------------------------------------------------------------------
# LoRa↔LoRa bridging
# ---------------------------------------------------------------------------

LORA_TO_LORA = """
lora:
  - id: mc-1
    type: meshcore
    connection:
      type: usb
      device_id: "0403:6015"
    endpoints:
      ch:
        type: public
        channel_name: "General"
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
  - id: mc-2
    type: meshcore
    connection:
      type: tcp
      host: "10.0.0.1"
      port: 5000
    endpoints:
      relay:
        type: public
        channel_name: "General"
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
messengers: []
rooms:
  - lora:
      node: mc-1
      endpoint: ch
    subscribers:
      - lora:
          node: mc-2
          endpoint: relay
"""


def test_lora_to_lora_config_parses():
    cfg = parse(LORA_TO_LORA)
    assert len(cfg.lora) == 2
    assert len(cfg.messengers) == 0
    room = cfg.rooms[0]
    assert room.lora.node == "mc-1"
    sub = room.subscribers[0]
    assert sub.lora.node == "mc-2"
    assert sub.lora.endpoint == "relay"


# ---------------------------------------------------------------------------
# Corner cases — невалидные YAML-конфиги
# ---------------------------------------------------------------------------


def test_room_server_without_password_valid():
    cfg = parse(
        _cfg_with(
            connection={"type": "tcp", "host": "h", "port": 1},
            endpoint={"type": "room_server", "pubkey": "abc123"},
        )
    )
    assert cfg.lora[0].endpoints["ch"].password is None
