"""Тесты dedup и loop-guard на LoRa-пути (A1/A3)."""

from lora_bridge.core.dedup import TtlDedup
from lora_bridge.core.loopguard import LoopGuard
from lora_bridge.domain.models import ChannelRef, Identity, Message
from tests.helpers.fakes import FakeClock


def _msg(text: str, uid: str = "u1", origin_tag: str | None = None) -> Message:
    return Message(
        id="x",
        source=ChannelRef("meshcore-1", "emergency"),
        sender=Identity(display_name="n", transport_uid=uid),
        text=text,
        origin_tag=origin_tag,
    )


def test_dedup_accepts_once_then_rejects_duplicate():
    clock = FakeClock()
    dd = TtlDedup(ttl_seconds=300, _clock=clock)
    assert dd.accept(_msg("привет")) is True
    assert dd.accept(_msg("привет")) is False  # дубль контента


def test_dedup_distinguishes_authors_with_same_text():
    # mesh-отправители делят один transport_uid (LORA_SENDER_UID), поэтому автора
    # различает display_name: два разных автора с одинаковым текстом — не дубль
    clock = FakeClock()
    dd = TtlDedup(ttl_seconds=300, _clock=clock)
    assert dd.accept(_msg("привет", uid="__lora__")) is True  # display_name="n"
    other = Message(
        id="y",
        source=ChannelRef("meshcore-1", "emergency"),
        sender=Identity(display_name="other", transport_uid="__lora__"),
        text="привет",
    )
    assert dd.accept(other) is True  # тот же текст, иной автор -> не дубль


def test_dedup_expires_after_ttl():
    clock = FakeClock()
    dd = TtlDedup(ttl_seconds=10, _clock=clock)
    assert dd.accept(_msg("hi")) is True
    clock.t = 11
    assert dd.accept(_msg("hi")) is True  # окно истекло → снова новое


def test_dedup_uses_origin_tag_when_present():
    clock = FakeClock()
    dd = TtlDedup(ttl_seconds=300, _clock=clock)
    assert dd.accept(_msg("a", origin_tag="pkt-1")) is True
    assert dd.accept(_msg("b", origin_tag="pkt-1")) is False  # тот же пакет, иной текст


def test_loopguard_detects_own_echo():
    clock = FakeClock()
    lg = LoopGuard(ttl_seconds=60, _clock=clock)
    lg.mark_sent("[TG:Alex] привет")
    assert lg.is_echo(_msg("[TG:Alex] привет")) is True
    assert lg.is_echo(_msg("чужое сообщение")) is False


def test_relay_reconstruction_stays_echo_detectable():
    # инвариант: relay восстанавливает автора как "[ник] текст" (скобки, без ": "),
    # поэтому при эхе обратно channel_to_message НЕ срежет это как автора, и текст
    # совпадёт с тем, что мы пометили mark_sent -> loop-guard опознает своё эхо.
    from lora_bridge.core.transform import relay_lora_text
    from lora_bridge.transports.meshcore.mappers.channel_util import channel_to_message

    relayed = Message(
        id="r",
        source=ChannelRef("meshcore-1", "general"),
        sender=Identity(display_name="Bob", transport_uid="__lora__"),
        text="из сети A",
    )
    wire = relay_lora_text(relayed)  # "[Bob] из сети A"
    lg = LoopGuard(ttl_seconds=60, _clock=FakeClock())
    lg.mark_sent(wire)

    echo = channel_to_message({"text": wire}, endpoint="general", node_id="meshcore-1")
    assert echo.text == wire  # автор НЕ вырезан (нет ": ") — текст цел
    assert lg.is_echo(echo) is True


def test_loopguard_echo_expires():
    clock = FakeClock()
    lg = LoopGuard(ttl_seconds=5, _clock=clock)
    lg.mark_sent("payload")
    clock.t = 6
    assert lg.is_echo(_msg("payload")) is False
