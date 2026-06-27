"""Характеризационные тесты для чистых функций, вынесенных из transport.py при
разбиении монолита MeshCore-адаптера: классификация ответа устройства (result.py)
и нормализация RX-событий в доменный ``Message`` (mappers/channel_util, mappers/room_server).

Цель — зафиксировать поведение ровно таким, каким оно было до разбиения: тесты
закрепляют контракт §5.1/AD-5 (busy vs failed) и формат id/sender для RX.
"""

from dataclasses import dataclass
from typing import Any

from lora_bridge.domain.models import LORA_SENDER_UID
from lora_bridge.transports.meshcore.mappers.channel_util import channel_to_message
from lora_bridge.transports.meshcore.mappers.room_server import room_server_to_message
from lora_bridge.transports.meshcore.result import classify


@dataclass
class FakeResult:
    """Имитация ответа meshcore_py: есть только is_error() и payload."""

    error: bool
    payload: Any

    def is_error(self) -> bool:
        return self.error


# --- classify (§5.1, AD-5): busy (повтор) vs failed ---


def test_classify_success() -> None:
    res = classify(FakeResult(error=False, payload={}))
    assert res.ok is True
    assert res.busy is False


def test_classify_table_full_is_busy_not_failed() -> None:
    # ERR_CODE_TABLE_FULL=3 → очередь узла полна, не FAILED, а повтор позже
    res = classify(FakeResult(error=True, payload={"error_code": 3}))
    assert res.ok is False
    assert res.busy is True


def test_classify_no_event_received_is_busy_with_reason() -> None:
    # устройство занято флудом — повтор, причина пробрасывается в detail
    res = classify(FakeResult(error=True, payload={"reason": "no_event_received"}))
    assert res.ok is False
    assert res.busy is True
    assert res.detail == "no_event_received"


def test_classify_other_error_is_failure() -> None:
    res = classify(FakeResult(error=True, payload={"code_string": "boom"}))
    assert res.ok is False
    assert res.busy is False
    assert res.detail == "boom"


def test_classify_error_with_reason_detail() -> None:
    # нет code_string → detail берётся из reason
    res = classify(FakeResult(error=True, payload={"reason": "weird"}))
    assert res.ok is False
    assert res.busy is False
    assert res.detail == "weird"


def test_classify_non_dict_payload_is_failure() -> None:
    # payload не dict → пустой dict → detail = str(payload)
    res = classify(FakeResult(error=True, payload="raw-error"))
    assert res.ok is False
    assert res.busy is False
    assert res.detail == "raw-error"


# --- channel_to_message (CHANNEL_MSG_RECV) ---


def test_channel_to_message_fields() -> None:
    msg = channel_to_message(
        {"sender_timestamp": 123, "text": "привет"}, endpoint="emergency", node_id="node-a"
    )
    assert msg.source.transport_id == "node-a"
    assert msg.source.channel == "emergency"
    # CHANNEL_MSG_RECV не несёт имени отправителя — callsign уже в тексте
    assert msg.sender.display_name == ""
    assert msg.sender.transport_uid == LORA_SENDER_UID
    assert msg.text == "привет"
    # id = "{endpoint}:{ts}:{hash(text)}"; hash(str) солится per-process — проверяем префикс
    assert msg.id.startswith("emergency:123:")


def test_channel_to_message_defaults() -> None:
    msg = channel_to_message({}, endpoint="ch", node_id="n")
    assert msg.text == ""
    assert msg.id.startswith("ch:0:")


# --- room_server_to_message (CONTACT_MSG_RECV) ---
#
# Автор room-server поста едет НЕ в тексте, а структурным полем `signature`
# (4-байтовый префикс ключа автора; подтверждено реальным пейлоадом: txt_type=2,
# signature=a096d337, text чистый). pubkey_prefix — это ключ КОМНАТЫ, не автора.
# Имя резолвится через `resolve_author(signature) -> adv_name`; fallback — сам hex.


def test_room_server_to_message_resolves_author_name() -> None:
    msg = room_server_to_message(
        {"pubkey_prefix": "eaa55fab2656", "signature": "a096d337",
         "text": "hi", "sender_timestamp": 999},
        endpoint="room1",
        node_id="node-b",
        resolve_author=lambda prefix: "Alice" if prefix == "a096d337" else None,
    )
    assert msg.source.transport_id == "node-b"
    assert msg.source.channel == "room1"
    # имя автора = резолв signature -> adv_name (НЕ pubkey_prefix комнаты)
    assert msg.sender.display_name == "Alice"
    assert msg.sender.transport_uid == LORA_SENDER_UID
    assert msg.text == "hi"
    # id детерминирован: "{endpoint}:{sender_timestamp}"
    assert msg.id == "room1:999"


def test_room_server_to_message_falls_back_to_hex_when_author_unknown() -> None:
    # автор вне таблицы контактов устройства -> резолвер вернул None -> hex-префикс
    msg = room_server_to_message(
        {"signature": "a096d337", "text": "hi", "sender_timestamp": 1},
        endpoint="room1",
        node_id="n",
        resolve_author=lambda _prefix: None,
    )
    assert msg.sender.display_name == "a096d337"


def test_room_server_to_message_falls_back_to_hex_without_resolver() -> None:
    msg = room_server_to_message(
        {"signature": "a096d337", "text": "hi"}, endpoint="r", node_id="n"
    )
    assert msg.sender.display_name == "a096d337"


def test_room_server_to_message_no_signature_has_no_author() -> None:
    # нет signature (напр. txt_type != 2) -> имя неизвестно, passthrough
    msg = room_server_to_message({"text": "hi"}, endpoint="r", node_id="n")
    assert msg.sender.display_name == ""
    assert msg.text == "hi"


def test_room_server_to_message_defaults() -> None:
    msg = room_server_to_message({}, endpoint="r", node_id="n")
    assert msg.sender.display_name == ""
    assert msg.text == ""
    assert msg.id == "r:0"
