"""Тесты кросс-валидации конфига (§12): ссылки rooms → существующие ноды/эндпоинты/мессенджеры."""

import pytest
from pydantic import ValidationError

from lora_bridge.config.schema import AppConfig

_BASE = {
    "lora": [
        {
            "id": "n1",
            "type": "meshcore",
            "connection": {"type": "tcp", "host": "h", "port": 1},
            "endpoints": {"emergency": {"type": "public", "channel_name": "General"}},
            "policies": {"egress_rate": {"msgs_per_window": 6, "window_seconds": 60}},
        }
    ],
    "messengers": [{"id": "tg", "kind": "telegram", "token": "t"}],
    "rooms": [
        {
            "lora": {"node": "n1", "endpoint": "emergency"},
            "subscribers": [{"transport": "tg", "chat": "-100"}],
        }
    ],
}


def _cfg(**over):
    import copy

    c = copy.deepcopy(_BASE)
    c.update(over)
    return c


def test_unknown_node_rejected():
    rooms = [
        {
            "lora": {"node": "nX", "endpoint": "emergency"},
            "subscribers": [{"transport": "tg", "chat": "-1"}],
        }
    ]
    with pytest.raises(ValidationError, match="неизвестная LoRa-нода"):
        AppConfig.model_validate(_cfg(rooms=rooms))


def test_unknown_endpoint_rejected():
    rooms = [
        {
            "lora": {"node": "n1", "endpoint": "nope"},
            "subscribers": [{"transport": "tg", "chat": "-1"}],
        }
    ]
    with pytest.raises(ValidationError, match="нет эндпоинта"):
        AppConfig.model_validate(_cfg(rooms=rooms))


def test_unknown_messenger_rejected():
    rooms = [
        {
            "lora": {"node": "n1", "endpoint": "emergency"},
            "subscribers": [{"transport": "ghost", "chat": "-1"}],
        }
    ]
    with pytest.raises(ValidationError, match="неизвестный мессенджер"):
        AppConfig.model_validate(_cfg(rooms=rooms))


def test_duplicate_node_id_rejected():
    lora = _BASE["lora"] + _BASE["lora"]
    with pytest.raises(ValidationError, match="дублирующиеся id"):
        AppConfig.model_validate(_cfg(lora=lora))


def test_duplicate_messenger_id_rejected():
    messengers = _BASE["messengers"] + _BASE["messengers"]
    with pytest.raises(ValidationError, match="дублирующиеся id"):
        AppConfig.model_validate(_cfg(messengers=messengers))


def test_lora_subscriber_unknown_node_rejected():
    rooms = [
        {
            "lora": {"node": "n1", "endpoint": "emergency"},
            "subscribers": [{"lora": {"node": "ghost", "endpoint": "relay"}}],
        }
    ]
    with pytest.raises(ValidationError, match="неизвестная LoRa-нода"):
        AppConfig.model_validate(_cfg(rooms=rooms))


def test_lora_subscriber_unknown_endpoint_rejected():
    rooms = [
        {
            "lora": {"node": "n1", "endpoint": "emergency"},
            "subscribers": [{"lora": {"node": "n1", "endpoint": "nope"}}],
        }
    ]
    with pytest.raises(ValidationError, match="нет эндпоинта"):
        AppConfig.model_validate(_cfg(rooms=rooms))
