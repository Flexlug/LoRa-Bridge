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


# --- запрет общего ChannelRef между Room (§12.2: точка роста, многосетевой кейс) ---


def test_messenger_chat_shared_between_rooms_rejected():
    """Один TG-чат в подписчиках двух Room — пока не поддержано (см. §12.2)."""
    lora = [
        _BASE["lora"][0],
        {
            "id": "n2",
            "type": "meshcore",
            "connection": {"type": "tcp", "host": "h", "port": 2},
            "endpoints": {"emergency": {"type": "public", "channel_name": "General"}},
            "policies": {"egress_rate": {"msgs_per_window": 6, "window_seconds": 60}},
        },
    ]
    rooms = [
        {
            "lora": {"node": "n1", "endpoint": "emergency"},
            "subscribers": [{"transport": "tg", "chat": "-100"}],
        },
        {
            "lora": {"node": "n2", "endpoint": "emergency"},
            "subscribers": [{"transport": "tg", "chat": "-100"}],
        },
    ]
    with pytest.raises(ValidationError, match="мессенджер-канал .* состоит в нескольких"):
        AppConfig.model_validate(_cfg(lora=lora, rooms=rooms))


def test_messenger_chat_same_chat_different_topics_allowed():
    """Один chat_id, но разные topic — это разные ChannelRef, допускаем."""
    lora = [
        _BASE["lora"][0],
        {
            "id": "n2",
            "type": "meshcore",
            "connection": {"type": "tcp", "host": "h", "port": 2},
            "endpoints": {"emergency": {"type": "public", "channel_name": "General"}},
            "policies": {"egress_rate": {"msgs_per_window": 6, "window_seconds": 60}},
        },
    ]
    rooms = [
        {
            "lora": {"node": "n1", "endpoint": "emergency"},
            "subscribers": [{"transport": "tg", "chat": "-100", "topic": "a"}],
        },
        {
            "lora": {"node": "n2", "endpoint": "emergency"},
            "subscribers": [{"transport": "tg", "chat": "-100", "topic": "b"}],
        },
    ]
    AppConfig.model_validate(_cfg(lora=lora, rooms=rooms))


def test_lora_endpoint_primary_in_two_rooms_rejected():
    rooms = [
        {
            "lora": {"node": "n1", "endpoint": "emergency"},
            "subscribers": [{"transport": "tg", "chat": "-100"}],
        },
        {
            "lora": {"node": "n1", "endpoint": "emergency"},
            "subscribers": [{"transport": "tg", "chat": "-200"}],
        },
    ]
    with pytest.raises(ValidationError, match="LoRa-эндпоинт .* состоит в нескольких"):
        AppConfig.model_validate(_cfg(rooms=rooms))


def test_lora_endpoint_as_subscriber_in_two_rooms_rejected():
    """Эндпоинт первичен в одной Room и подписчик в другой — конфликт."""
    lora = [
        {
            "id": "n1",
            "type": "meshcore",
            "connection": {"type": "tcp", "host": "h", "port": 1},
            "endpoints": {
                "emergency": {"type": "public", "channel_name": "General"},
                "relay": {"type": "public", "channel_name": "Relay"},
            },
            "policies": {"egress_rate": {"msgs_per_window": 6, "window_seconds": 60}},
        },
        {
            "id": "n2",
            "type": "meshcore",
            "connection": {"type": "tcp", "host": "h", "port": 2},
            "endpoints": {"emergency": {"type": "public", "channel_name": "General"}},
            "policies": {"egress_rate": {"msgs_per_window": 6, "window_seconds": 60}},
        },
    ]
    rooms = [
        {
            "lora": {"node": "n1", "endpoint": "emergency"},
            "subscribers": [{"transport": "tg", "chat": "-100"}],
        },
        {
            "lora": {"node": "n2", "endpoint": "emergency"},
            "subscribers": [{"lora": {"node": "n1", "endpoint": "emergency"}}],
        },
    ]
    with pytest.raises(ValidationError, match="LoRa-эндпоинт .* состоит в нескольких"):
        AppConfig.model_validate(_cfg(lora=lora, rooms=rooms))
