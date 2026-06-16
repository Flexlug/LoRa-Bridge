"""Сценарные тесты ``format_validation_error``.

Каждый тест собирает заведомо плохой YAML, прогоняет через ``AppConfig.model_validate``,
ловит ``ValidationError`` и проверяет, что итоговый текст содержит ключевые подсказки —
расположение ошибки, ожидаемый тип / варианты, список соседних полей.
"""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from lora_bridge.config.errors import format_validation_error
from lora_bridge.config.schema import AppConfig


def _format(text: str) -> str:
    try:
        AppConfig.model_validate(yaml.safe_load(text))
    except ValidationError as exc:
        return format_validation_error(exc)
    pytest.fail("ожидалась ValidationError")


def _yaml(*, connection: str = "{ type: tcp, host: h, port: 1 }",
          endpoint: str = "{ type: public, channel_name: G }",
          extra_lora: str = "", extra_top: str = "", rooms: str = "rooms: []",
          messengers: str = "messengers: []", policies_extra: str = "") -> str:
    return f"""
lora:
  - id: n1
    type: meshcore
    connection: {connection}
    endpoints: {{ ch: {endpoint} }}
    policies: {{ egress_rate: {{ msgs_per_window: 6, window_seconds: 60 }}{policies_extra} }}
{extra_lora}
{messengers}
{rooms}
{extra_top}
"""


# ---------------------------------------------------------------------------
# discriminated union: неизвестный тег (connection / endpoint)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "yml,path,bad_tag,valid_tags",
    [
        (
            _yaml(connection="{ type: bluetooth, address: x }"),
            "lora[0].connection",
            "bluetooth",
            ("usb", "serial", "tcp", "ble"),
        ),
        (
            _yaml(endpoint="{ type: multicast }"),
            "lora[0].endpoints.ch",
            "multicast",
            ("public", "private", "room_server"),
        ),
    ],
)
def test_unknown_discriminator_tag_lists_valid_tags(yml, path, bad_tag, valid_tags):
    msg = _format(yml)
    assert path in msg
    assert f"'{bad_tag}'" in msg
    for tag in valid_tags:
        assert f"'{tag}'" in msg


# ---------------------------------------------------------------------------
# missing: пропущенное обязательное поле + соседи в той же модели
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "yml,path,variant,siblings",
    [
        (
            _yaml(connection="{ type: tcp, port: 5050 }"),
            "lora[0].connection.host",
            "TcpConnection",
            ("port",),
        ),
        (
            _yaml(endpoint="{ type: private, channel_name: Ops }"),
            "lora[0].endpoints.ch.secret",
            "PrivateEndpoint",
            ("channel_name", "secret"),
        ),
    ],
)
def test_missing_required_field_names_variant_and_siblings(yml, path, variant, siblings):
    msg = _format(yml)
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
        _yaml(
            messengers="messengers: [{ id: tg, kind: telegram, token: t }]",
            rooms=(
                "rooms:\n"
                "  - lora: { node: n1, endpoint: ch }\n"
                "    subscribers: [{ transport: tg, chat: \"-1\", oops: 1 }]"
            ),
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
    msg = _format(_yaml(connection="{ type: tcp, host: h, port: notanint }"))
    assert "lora[0].connection.port" in msg
    assert "целое число" in msg
    assert "'notanint'" in msg


def test_literal_error_shows_allowed_values():
    msg = _format(
        _yaml(policies_extra=", label: { include_type: maybe }"),
    )
    assert "lora[0].policies.label.include_type" in msg
    assert "'maybe'" in msg
    for v in ("auto", "always", "never"):
        assert f"'{v}'" in msg


# ---------------------------------------------------------------------------
# value_error из model_validator (cross-ref / форма комнаты)
# ---------------------------------------------------------------------------


_BAD_NODE_REF = _yaml(
    messengers="messengers: [{ id: tg, kind: telegram, token: t }]",
    rooms=(
        "rooms:\n"
        "  - lora: { node: WRONG, endpoint: ch }\n"
        "    subscribers: [{ transport: tg, chat: \"-1\" }]"
    ),
)

_BAD_ROOM_SHAPE = _yaml(
    extra_lora=(
        "  - id: n2\n"
        "    type: meshcore\n"
        "    connection: { type: tcp, host: h, port: 2 }\n"
        "    endpoints: { ch: { type: public, channel_name: G } }\n"
        "    policies: { egress_rate: { msgs_per_window: 6, window_seconds: 60 } }"
    ),
    messengers="messengers: [{ id: tg, kind: telegram, token: t }]",
    rooms=(
        "rooms:\n"
        "  - lora: { node: n1, endpoint: ch }\n"
        "    subscribers:\n"
        "      - { transport: tg, chat: \"-1\" }\n"
        "      - { lora: { node: n2, endpoint: ch } }"
    ),
)


@pytest.mark.parametrize(
    "yml,expected_path,expected_substr",
    [
        (_BAD_NODE_REF, None, "неизвестная LoRa-нода 'WRONG'"),
        (_BAD_ROOM_SHAPE, "rooms[0]", "LoRa↔LoRa-комната"),
    ],
)
def test_model_validator_message_passes_through_without_value_error_prefix(
    yml, expected_path, expected_substr
):
    msg = _format(yml)
    assert expected_substr in msg
    if expected_path is not None:
        assert expected_path in msg
    assert "Value error," not in msg


# ---------------------------------------------------------------------------
# несколько ошибок одновременно
# ---------------------------------------------------------------------------


def test_multiple_errors_all_reported_and_numbered():
    msg = _format(
        _yaml(connection="{ type: tcp, port: 5050 }", endpoint="{ type: public }"),
    )
    assert "1. lora[0].connection.host" in msg
    assert "lora[0].endpoints.ch.channel_name" in msg
    assert "ошибки" in msg or "ошибок" in msg
