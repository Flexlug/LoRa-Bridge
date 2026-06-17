"""Узкоюнитовые тесты ``lora_bridge.config.errors``.

Два пласта проверок:

1. Чистые хелперы форматтера (плюрализация, путь, рендер значения, рендер типа,
   group-by-вариант, walker по типовому дереву) — тестируем напрямую.
2. Свойства читаемости итогового сообщения: шапка, нумерация, путь — YAML-like,
   нет утёкшего технического шума (имена дискриминаторных тегов, pydantic-префиксы).
"""

from __future__ import annotations

from typing import Annotated

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from lora_bridge.config.errors import (
    _collapse_smart_unions,
    _format_input,
    _format_loc,
    _format_one,
    _plural_errors,
    _pretty_type,
    _resolve_models,
    _smart_union_variant_index,
    _strip_annotated,
    format_validation_error,
)
from lora_bridge.config.schema import (
    AppConfig,
    MessengerSubscriber,
    PrivateEndpoint,
    PublicEndpoint,
    RoomServerEndpoint,
    TcpConnection,
    TelegramMessengerConfig,
    UsbConnection,
)


# ===========================================================================
# 1. Чистые хелперы
# ===========================================================================


@pytest.mark.parametrize(
    "n,expected",
    [
        (1, "ошибка"), (21, "ошибка"), (101, "ошибка"),
        (2, "ошибки"), (3, "ошибки"), (4, "ошибки"), (22, "ошибки"), (104, "ошибки"),
        (5, "ошибок"), (10, "ошибок"),
        (11, "ошибок"), (12, "ошибок"), (13, "ошибок"), (14, "ошибок"),
        (20, "ошибок"), (100, "ошибок"), (111, "ошибок"),
    ],
)
def test_plural_errors_matches_russian_grammar(n, expected):
    assert _plural_errors(n) == expected


@pytest.mark.parametrize(
    "loc,expected",
    [
        ((), "(корень конфига)"),
        (("lora",), "lora"),
        (("lora", 0), "lora[0]"),
        (("lora", 0, "id"), "lora[0].id"),
        # discriminator-тег ('tcp') и smart-union вариант ('MessengerSubscriber') скрыты
        (("lora", 0, "connection", "tcp", "host"), "lora[0].connection.host"),
        (
            ("rooms", 0, "subscribers", 0, "MessengerSubscriber", "chat"),
            "rooms[0].subscribers[0].chat",
        ),
        # ключ dict (имя эндпоинта) — не имя варианта, не должен скрываться
        (
            ("lora", 0, "endpoints", "general", "channel_name"),
            "lora[0].endpoints.general.channel_name",
        ),
    ],
)
def test_format_loc_renders_yaml_like_path(loc, expected):
    assert _format_loc(loc) == expected


@pytest.mark.parametrize(
    "inp,expected",
    [
        (None, "null"),
        ("hello", "'hello'"),
        (42, "42"),
    ],
)
def test_format_input_renders_value_for_user(inp, expected):
    assert _format_input(inp) == expected


def test_format_input_truncates_long_values():
    rendered = _format_input("x" * 200)
    assert rendered.endswith("...")
    assert len(rendered) <= 80


@pytest.mark.parametrize(
    "t,expected",
    [
        (str, "строка"),
        (int, "целое число"),
        (list[str], "список строка"),
        (dict[str, int], "словарь строка → целое число"),
    ],
)
def test_pretty_type_renders_russian_label(t, expected):
    assert _pretty_type(t) == expected


@pytest.mark.parametrize(
    "t,expected",
    [
        (int, int),
        (Annotated[int, "marker"], int),
    ],
)
def test_strip_annotated_unwraps_to_raw_type(t, expected):
    assert _strip_annotated(t) is expected


@pytest.mark.parametrize(
    "loc,expected",
    [
        (("rooms", 0, "subscribers", 0, "MessengerSubscriber", "chat"), 4),
        (("lora", 0, "connection", "host"), None),
    ],
)
def test_smart_union_variant_index_detects_pascal_case(loc, expected):
    assert _smart_union_variant_index(loc) == expected


@pytest.mark.parametrize(
    "loc,check",
    [
        # dict[str, Endpoint] → Union[Public, Private, RoomServer] — три кандидата
        (
            ("lora", 0, "endpoints", "general"),
            lambda models: set(models) == {PublicEndpoint, PrivateEndpoint, RoomServerEndpoint},
        ),
        # сужение Union по тегу дискриминатора
        (("lora", 0, "connection", "tcp"), lambda models: models == [TcpConnection]),
        # сужение Union по имени класса (smart-union)
        (
            ("rooms", 0, "subscribers", 0, "MessengerSubscriber"),
            lambda models: models == [MessengerSubscriber],
        ),
        # несуществующее поле → пусто
        (("lora", 0, "nonexistent_field"), lambda models: models == []),
        # вершина списка мессенджеров
        (("messengers", 0), lambda models: TelegramMessengerConfig in models),
    ],
)
def test_resolve_models_walks_type_tree(loc, check):
    assert check(_resolve_models(loc))


# --- _collapse_smart_unions ------------------------------------------------


def _err(loc, kind="extra_forbidden", inp=1):
    return {"type": kind, "loc": loc, "msg": "x", "input": inp}


def test_collapse_keeps_only_smallest_variant_per_union_point():
    errors = [
        # MessengerSubscriber: 1 ошибка — победитель
        _err(("rooms", 0, "subscribers", 0, "MessengerSubscriber", "oops")),
        # LoraSubscriber: 3 ошибки — отбрасываются
        _err(("rooms", 0, "subscribers", 0, "LoraSubscriber", "lora"), "missing", {}),
        _err(("rooms", 0, "subscribers", 0, "LoraSubscriber", "transport"), inp="tg"),
        _err(("rooms", 0, "subscribers", 0, "LoraSubscriber", "chat"), inp="-1"),
    ]
    collapsed = _collapse_smart_unions(errors)
    assert len(collapsed) == 1
    assert "MessengerSubscriber" in collapsed[0]["loc"]


def test_collapse_passes_through_errors_without_union_variant():
    errors = [_err(("lora", 0, "connection", "tcp", "host"), "missing", {})]
    assert _collapse_smart_unions(errors) == errors


def test_collapse_handles_multiple_union_points_independently():
    errors = [
        # union точка #1 — MessengerSubscriber (1 ошибка) < LoraSubscriber (2)
        _err(("rooms", 0, "subscribers", 0, "MessengerSubscriber", "oops")),
        _err(("rooms", 0, "subscribers", 0, "LoraSubscriber", "lora"), "missing", {}),
        _err(("rooms", 0, "subscribers", 0, "LoraSubscriber", "x")),
        # union точка #2 — LoraSubscriber (1) < MessengerSubscriber (2)
        _err(("rooms", 1, "subscribers", 0, "LoraSubscriber", "lora"), "missing", {}),
        _err(("rooms", 1, "subscribers", 0, "MessengerSubscriber", "a")),
        _err(("rooms", 1, "subscribers", 0, "MessengerSubscriber", "b")),
    ]
    collapsed = _collapse_smart_unions(errors)
    variants = sorted({e["loc"][4] for e in collapsed})
    assert variants == ["LoraSubscriber", "MessengerSubscriber"]


# ===========================================================================
# 2. Свойства читаемости итогового текста
# ===========================================================================


def _render(text: str) -> str:
    try:
        AppConfig.model_validate(yaml.safe_load(text))
    except ValidationError as exc:
        return format_validation_error(exc)
    pytest.fail("ожидалась ValidationError")


_TINY_BAD = """
lora:
  - id: n1
    type: meshcore
    connection:
      type: tcp
      port: 5050
    endpoints:
      ch:
        type: public
        channel_name: G
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
messengers: []
rooms: []
"""

_TWO_ERRORS = """
lora:
  - id: n1
    type: meshcore
    connection:
      type: tcp
      port: 5050
    endpoints:
      ch:
        type: public
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
messengers: []
rooms: []
"""


def test_header_contains_russian_intro_count_and_correct_plural():
    first_line = _render(_TINY_BAD).splitlines()[0]
    assert first_line.startswith("Конфиг не прошёл валидацию")
    assert "1 ошибка" in first_line


def test_each_error_block_is_numbered():
    msg = _render(_TWO_ERRORS)
    assert "1. " in msg
    assert "2. " in msg


def test_path_uses_yaml_friendly_notation():
    msg = _render(_TINY_BAD)
    assert "lora[0].connection.host" in msg
    assert ".tcp.host" not in msg  # вариантный сегмент не утёк


@pytest.mark.parametrize(
    "yml,forbidden",
    [
        # «Value error, » префикс из pydantic
        (
            """
lora:
  - id: n1
    type: meshcore
    connection:
      type: tcp
      host: h
      port: 1
    endpoints:
      ch:
        type: public
        channel_name: G
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
messengers:
  - id: tg
    kind: telegram
    token: t
rooms:
  - lora:
      node: WRONG
      endpoint: ch
    subscribers:
      - transport: tg
        chat: "-1"
""",
            ("Value error,",),
        ),
        # URL на документацию pydantic
        (_TINY_BAD, ("errors.pydantic.dev",)),
        # внутренние имена видов ошибок pydantic
        (
            """
lora:
  - id: n1
    type: meshcore
    connection:
      type: bluetooth
      address: x
    endpoints:
      ch:
        type: public
        channel_name: G
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
messengers: []
rooms: []
""",
            ("union_tag_invalid", "extra_forbidden", "int_parsing", "missing"),
        ),
    ],
)
def test_no_technical_pydantic_noise_leaks(yml, forbidden):
    msg = _render(yml)
    for token in forbidden:
        assert token not in msg, f"в выводе утёк токен {token!r}:\n{msg}"


def test_output_layout_is_multiline_with_dash_bullets_and_single_trailing_newline():
    msg = _render(_TINY_BAD)
    lines = [ln for ln in msg.splitlines() if ln.strip()]
    assert len(lines) >= 3  # шапка, заголовок ошибки, минимум один пункт
    assert any(ln.lstrip().startswith("—") for ln in lines)
    assert msg.endswith("\n")
    assert not msg.endswith("\n\n")


@pytest.mark.parametrize(
    "yml,must_contain",
    [
        # missing: имя поля + сосед в той же модели
        (_TINY_BAD, ["обязательное поле", "'host'", "port"]),
        # unknown discriminator: все валидные теги
        (
            """
lora:
  - id: n1
    type: meshcore
    connection:
      type: bluetooth
      address: x
    endpoints:
      ch:
        type: public
        channel_name: G
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
messengers: []
rooms: []
""",
            ["'bluetooth'", "'usb'", "'serial'", "'tcp'", "'ble'"],
        ),
        # int_parsing: ожидаемый тип + полученное значение
        (
            """
lora:
  - id: n1
    type: meshcore
    connection:
      type: tcp
      host: h
      port: notanint
    endpoints:
      ch:
        type: public
        channel_name: G
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
messengers: []
rooms: []
""",
            ["целое число", "'notanint'"],
        ),
    ],
)
def test_each_kind_carries_actionable_hints(yml, must_contain):
    msg = _render(yml)
    for piece in must_contain:
        assert piece in msg, f"нет ключевой подсказки {piece!r}:\n{msg}"


# ===========================================================================
# 3. Гарантии вокруг публичного API
# ===========================================================================


def test_format_validation_error_returns_nonempty_str():
    msg = _render(_TINY_BAD)
    assert isinstance(msg, str) and msg


def test_format_one_does_not_raise_on_unknown_pydantic_kind():
    """Если pydantic подсунет неизвестный type — fallback ветка не падает."""

    class _M(BaseModel):
        x: int

    try:
        _M.model_validate({"x": "not an int"})
    except ValidationError as exc:
        err = {**list(exc.errors())[0], "type": "totally_unknown_pydantic_kind"}
        rendered = "\n".join(_format_one(err, 1))
        assert rendered.startswith("1. ")
        # сырое сообщение pydantic пробрасывается как есть
        assert "Input should be a valid integer" in rendered


def test_usb_endpoint_missing_device_id_uses_correct_variant_label():
    """Регрессия: walker должен распознать вариант UsbConnection по тегу 'usb'."""
    yml = """
lora:
  - id: n1
    type: meshcore
    connection:
      type: usb
    endpoints:
      ch:
        type: public
        channel_name: G
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
messengers: []
rooms: []
"""
    msg = _render(yml)
    assert "UsbConnection" in msg
    assert "device_id" in msg
    assert "TcpConnection" not in msg and "SerialConnection" not in msg
    assert UsbConnection in _resolve_models(("lora", 0, "connection", "usb"))
