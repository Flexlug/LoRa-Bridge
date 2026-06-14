"""Тесты сборки LoRa-нагрузки и байтовых лимитов (§4, AD-10/AD-11, D2/D6)."""

from lora_bridge.core.transform import (
    build_lora_text,
    clip_utf8,
    oversize_bytes,
    utf8_len,
)
from lora_bridge.domain.models import ChannelRef, Identity, LabelFormat, Message, Room


def _msg(name: str, text: str) -> Message:
    return Message(
        id="1",
        source=ChannelRef("telegram-main", "emergency"),
        sender=Identity(display_name=name, transport_uid="u1"),
        text=text,
    )


def test_utf8_len_counts_bytes_not_chars():
    # кириллица: 2 байта на символ в UTF-8 (D2)
    assert utf8_len("привет") == 12
    assert utf8_len("hi") == 2


def test_clip_utf8_respects_byte_budget_without_splitting():
    clipped = clip_utf8("привет", 5)  # 5 байт = 2 символа + «хвост» отброшен
    assert utf8_len(clipped) <= 5
    assert clipped == "пр"


def test_clip_utf8_passthrough_when_fits():
    assert clip_utf8("Alex", 24) == "Alex"


def test_prefix_includes_type_when_multiple_messengers():
    room = Room(lora_endpoint="emergency", writable_messenger_count=2)
    fmt = LabelFormat(include_type=True, max_nick_bytes=24)
    out = build_lora_text(_msg("Alex", "привет"), room, tag="TG", fmt=fmt)
    assert out == "[TG:Alex] привет"


def test_prefix_omits_type_when_single_messenger():
    room = Room(lora_endpoint="emergency", writable_messenger_count=1)
    fmt = LabelFormat(include_type=True, max_nick_bytes=24)
    out = build_lora_text(_msg("Alex", "привет"), room, tag="TG", fmt=fmt)
    assert out == "[Alex] привет"


def test_text_is_never_truncated():
    # длинный текст не трогаем — это забота admission (TOO_LONG), не transform (AD-11)
    room = Room(lora_endpoint="emergency", writable_messenger_count=1)
    fmt = LabelFormat(max_nick_bytes=4)
    long_text = "x" * 500
    out = build_lora_text(_msg("Alexander", long_text), room, tag="TG", fmt=fmt)
    assert long_text in out


def test_oversize_bytes_positive_when_over_limit():
    assert oversize_bytes("x" * 151, 150) == 1
    assert oversize_bytes("ok", 150) < 0
