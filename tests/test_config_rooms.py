"""Тесты жёсткого инварианта формы комнаты (§12.1).

Допустимо ТОЛЬКО: «1 LoRa + N мессенджеров» или «2 LoRa + 0 мессенджеров».
"""
import pytest
from pydantic import ValidationError

from lora_bridge.config.schema import RoomConfig

_PRIMARY = {"node": "meshcore-1", "endpoint": "general"}
_MSG = {"transport": "telegram-main", "chat": "-100", "topic": "42"}
_PEER = {"lora": {"node": "meshcore-2", "endpoint": "relay"}}


def test_one_lora_plus_messengers_ok():
    room = RoomConfig.model_validate(
        {"lora": _PRIMARY, "subscribers": [_MSG, {"transport": "telegram-main", "chat": "-200"}]}
    )
    assert len(room.subscribers) == 2


def test_two_lora_zero_messengers_ok():
    room = RoomConfig.model_validate({"lora": _PRIMARY, "subscribers": [_PEER]})
    assert room.subscribers[0].lora.node == "meshcore-2"


def test_mixed_lora_and_messenger_rejected():
    with pytest.raises(ValidationError):
        RoomConfig.model_validate({"lora": _PRIMARY, "subscribers": [_MSG, _PEER]})


def test_more_than_two_lora_rejected():
    second_peer = {"lora": {"node": "meshcore-3", "endpoint": "relay"}}
    with pytest.raises(ValidationError):
        RoomConfig.model_validate({"lora": _PRIMARY, "subscribers": [_PEER, second_peer]})


def test_no_subscribers_rejected():
    with pytest.raises(ValidationError):
        RoomConfig.model_validate({"lora": _PRIMARY, "subscribers": []})


def test_self_loop_rejected():
    self_peer = {"lora": dict(_PRIMARY)}
    with pytest.raises(ValidationError):
        RoomConfig.model_validate({"lora": _PRIMARY, "subscribers": [self_peer]})


def test_unknown_subscriber_field_rejected():
    # extra=forbid → опечатка в подписчике не проглатывается молча
    with pytest.raises(ValidationError):
        RoomConfig.model_validate(
            {"lora": _PRIMARY, "subscribers": [{"transport": "tg", "chat": "-1", "topik": "42"}]}
        )
