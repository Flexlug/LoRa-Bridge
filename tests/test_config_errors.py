"""Сценарные тесты ``format_validation_error``.

Каждый тест собирает заведомо плохой конфиг как Python-dict, дампит его в YAML,
прогоняет через ``AppConfig.model_validate``, ловит ``ValidationError`` и
проверяет, что итоговый текст содержит ключевые подсказки — расположение
ошибки, ожидаемый тип / варианты, список соседних полей.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from lora_bridge.config.errors import format_validation_error
from lora_bridge.config.schema import AppConfig


def _format(cfg: dict[str, Any]) -> str:
    text = yaml.safe_dump(cfg, default_flow_style=False, sort_keys=False)
    try:
        AppConfig.model_validate(yaml.safe_load(text))
    except ValidationError as exc:
        return format_validation_error(exc)
    pytest.fail("ожидалась ValidationError")


def _cfg(
    *,
    connection: dict[str, Any] | None = None,
    endpoint: dict[str, Any] | None = None,
    extra_lora_nodes: list[dict[str, Any]] | None = None,
    messengers: list[dict[str, Any]] | None = None,
    rooms: list[dict[str, Any]] | None = None,
    policies_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Собрать конфиг с дефолтной правильной структурой + точечные подмены."""
    policies = {"egress_rate": {"msgs_per_window": 6, "window_seconds": 60}}
    if policies_extra:
        policies.update(policies_extra)
    node = {
        "id": "n1",
        "type": "meshcore",
        "connection": connection or {"type": "tcp", "host": "h", "port": 1},
        "endpoints": {"ch": endpoint or {"type": "public", "channel_name": "G"}},
        "policies": policies,
    }
    return {
        "lora": [node, *(extra_lora_nodes or [])],
        "messengers": messengers or [],
        "rooms": rooms or [],
    }


# ---------------------------------------------------------------------------
# discriminated union: неизвестный тег (connection / endpoint)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cfg,path,bad_tag,valid_tags",
    [
        (
            _cfg(connection={"type": "bluetooth", "address": "x"}),
            "lora[0].connection",
            "bluetooth",
            ("usb", "serial", "tcp", "ble"),
        ),
        (
            _cfg(endpoint={"type": "multicast"}),
            "lora[0].endpoints.ch",
            "multicast",
            ("public", "private", "room_server"),
        ),
    ],
)
def test_unknown_discriminator_tag_lists_valid_tags(cfg, path, bad_tag, valid_tags):
    msg = _format(cfg)
    assert path in msg
    assert f"'{bad_tag}'" in msg
    for tag in valid_tags:
        assert f"'{tag}'" in msg


# ---------------------------------------------------------------------------
# missing: пропущенное обязательное поле + соседи в той же модели
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cfg,path,variant,siblings",
    [
        (
            _cfg(connection={"type": "tcp", "port": 5050}),
            "lora[0].connection.host",
            "TcpConnection",
            ("port",),
        ),
        (
            _cfg(endpoint={"type": "private", "channel_name": "Ops"}),
            "lora[0].endpoints.ch.secret",
            "PrivateEndpoint",
            ("channel_name", "secret"),
        ),
    ],
)
def test_missing_required_field_names_variant_and_siblings(cfg, path, variant, siblings):
    msg = _format(cfg)
    assert path in msg
    assert "обязательное поле" in msg
    assert variant in msg
    for sib in siblings:
        assert sib in msg


# ---------------------------------------------------------------------------
# extra_forbidden: лишнее поле + перечисление допустимых (smart union case)
# ---------------------------------------------------------------------------


def test_extra_field_in_subscriber_lists_allowed_fields_of_best_variant():
    msg = _format(
        _cfg(
            messengers=[{"id": "tg", "kind": "telegram", "token": "t"}],
            rooms=[
                {
                    "lora": {"node": "n1", "endpoint": "ch"},
                    "subscribers": [
                        {"transport": "tg", "chat": "-1", "oops": 1},
                    ],
                }
            ],
        )
    )
    assert "rooms[0].subscribers[0].oops" in msg
    assert "не допускается" in msg
    # smart-union должен сузиться до MessengerSubscriber, не флудить LoraSubscriber-ошибками
    assert "MessengerSubscriber" in msg
    assert "transport" in msg
    assert "chat" in msg
    assert "LoraSubscriber" not in msg


# ---------------------------------------------------------------------------
# int_parsing / literal_error
# ---------------------------------------------------------------------------


def test_int_parsing_error_shows_expected_type_and_input():
    msg = _format(_cfg(connection={"type": "tcp", "host": "h", "port": "notanint"}))
    assert "lora[0].connection.port" in msg
    assert "целое число" in msg
    assert "'notanint'" in msg


def test_literal_error_shows_allowed_values():
    msg = _format(_cfg(policies_extra={"label": {"include_type": "maybe"}}))
    assert "lora[0].policies.label.include_type" in msg
    assert "'maybe'" in msg
    for v in ("auto", "always", "never"):
        assert f"'{v}'" in msg


# ---------------------------------------------------------------------------
# value_error из model_validator (cross-ref / форма комнаты)
# ---------------------------------------------------------------------------


_BAD_NODE_REF = _cfg(
    messengers=[{"id": "tg", "kind": "telegram", "token": "t"}],
    rooms=[
        {
            "lora": {"node": "WRONG", "endpoint": "ch"},
            "subscribers": [{"transport": "tg", "chat": "-1"}],
        }
    ],
)

_BAD_ROOM_SHAPE = _cfg(
    extra_lora_nodes=[
        {
            "id": "n2",
            "type": "meshcore",
            "connection": {"type": "tcp", "host": "h", "port": 2},
            "endpoints": {"ch": {"type": "public", "channel_name": "G"}},
            "policies": {"egress_rate": {"msgs_per_window": 6, "window_seconds": 60}},
        }
    ],
    messengers=[{"id": "tg", "kind": "telegram", "token": "t"}],
    rooms=[
        {
            "lora": {"node": "n1", "endpoint": "ch"},
            "subscribers": [
                {"transport": "tg", "chat": "-1"},
                {"lora": {"node": "n2", "endpoint": "ch"}},
            ],
        }
    ],
)


@pytest.mark.parametrize(
    "cfg,expected_path,expected_substr",
    [
        (_BAD_NODE_REF, None, "неизвестная LoRa-нода 'WRONG'"),
        (_BAD_ROOM_SHAPE, "rooms[0]", "LoRa↔LoRa-комната"),
    ],
)
def test_model_validator_message_passes_through_without_value_error_prefix(
    cfg, expected_path, expected_substr
):
    msg = _format(cfg)
    assert expected_substr in msg
    if expected_path is not None:
        assert expected_path in msg
    assert "Value error," not in msg


# ---------------------------------------------------------------------------
# несколько ошибок одновременно
# ---------------------------------------------------------------------------


def test_multiple_errors_all_reported_and_numbered():
    msg = _format(
        _cfg(connection={"type": "tcp", "port": 5050}, endpoint={"type": "public"}),
    )
    assert "1. lora[0].connection.host" in msg
    assert "lora[0].endpoints.ch.channel_name" in msg
    assert "ошибки" in msg or "ошибок" in msg
