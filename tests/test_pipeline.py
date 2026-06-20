"""Интеграционные тесты ядра на fake-транспортах (§6, §12.1).

Проверяем: admission → commit → статусы → post-commit миррор; LoRa↔LoRa relay;
отказы TOO_LONG / RATE_LIMIT.
"""

import pytest

from lora_bridge.core.bridge import Bridge, MessengerBinding, NodeRuntime
from lora_bridge.core.dedup import TtlDedup
from lora_bridge.core.journal import SqliteJournal
from lora_bridge.core.loopguard import LoopGuard
from lora_bridge.core.notifier import DropNotifier
from lora_bridge.core.queue import CommitQueue
from lora_bridge.core.routing import LoraMember, MessengerMember, RoomRegistry, RoomRoute
from lora_bridge.core.status import StatusDispatcher
from lora_bridge.domain.models import (
    ChannelRef,
    DeliveryStatus,
    Identity,
    LabelFormat,
    Message,
    RateSpec,
)
from tests.helpers.fakes import FakeTransport, LORA_CAPS, MSG_CAPS

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


_OPEN_JOURNALS: list = []


@pytest.fixture(autouse=True)
async def _close_journals():
    yield
    for j in _OPEN_JOURNALS:
        await j.stop()
    _OPEN_JOURNALS.clear()


def _msg(transport_id: str, channel: str, text: str, mid: str = "m1") -> Message:
    return Message(
        id=mid,
        source=ChannelRef(transport_id, channel),
        sender=Identity(display_name="Alex", transport_uid="u1"),
        text=text,
    )


async def _build(routes, nodes_transports, messengers, *, capacity=16, rate=RateSpec(100, 60)):
    journal = SqliteJournal(":memory:")
    await journal.start()
    _OPEN_JOURNALS.append(journal)
    notices: list = []

    async def sink(ref, text):
        notices.append((ref, text))

    nodes = {
        nid: NodeRuntime(
            transport=t,
            queue=CommitQueue(capacity, rate, ttl_seconds=45),
            dedup=TtlDedup(300),
            loop_guard=LoopGuard(60),
            label_fmt=LabelFormat(include_type=True, max_nick_bytes=24),
            commit_timeout=5,
            notifier=DropNotifier(60, sink),
        )
        for nid, t in nodes_transports.items()
    }
    all_transports = {**{nid: t for nid, t in nodes_transports.items()},
                      **{mid: t for mid, t in messengers.items()}}
    bindings = {mid: MessengerBinding(transport=t, tag="TG") for mid, t in messengers.items()}

    bridge = Bridge(
        nodes=nodes,
        messengers=bindings,
        rooms=RoomRegistry(routes),
        status=StatusDispatcher(all_transports),
        journal=journal,
    )
    return bridge, nodes, notices


async def test_messenger_to_lora_commit_and_mirror():
    lora = FakeTransport("n1", LORA_CAPS)
    m1 = FakeTransport("tg", MSG_CAPS)
    messengers = {"tg": m1}
    room = RoomRoute(
        members=(
            LoraMember("n1", "emergency"),
            MessengerMember("tg", "-100", "42"),
            MessengerMember("tg", "-200", None),
        )
    )
    bridge, nodes, _ = await _build([room], {"n1": lora}, messengers)

    src = _msg("tg", "-100#42", "привет")
    await bridge.admit(src)

    # статус источника PENDING, элемент в очереди
    assert m1.statuses[-1][1] == DeliveryStatus.PENDING

    await nodes["n1"].queue.close_input()
    await bridge.build_worker(nodes["n1"]).run()

    # ушло в LoRa с префиксом [TG:Alex] (в комнате >1 мессенджера)
    assert len(lora.sent) == 1
    assert lora.sent[0][1].text == "[TG:Alex] привет"
    # терминальный статус SENT
    assert m1.statuses[-1][1] == DeliveryStatus.SENT
    # миррор оригинала во второй чат (не в источник)
    assert any(t.channel == "-200" for t, _ in m1.sent)
    assert all(t.channel != "-100#42" for t, _ in m1.sent)


async def test_lora_to_lora_relay():
    a = FakeTransport("n1", LORA_CAPS)
    b = FakeTransport("n2", LORA_CAPS)
    room = RoomRoute(members=(LoraMember("n1", "general"), LoraMember("n2", "relay")))
    bridge, nodes, _ = await _build([room], {"n1": a, "n2": b}, {})

    # сообщение пришло на A → должно уйти на B как есть (без ре-префикса)
    src = _msg("n1", "general", "[Bob] из сети A")
    await bridge.route_from_lora(src)

    await nodes["n2"].queue.close_input()
    await bridge.build_worker(nodes["n2"]).run()

    assert len(b.sent) == 1
    assert b.sent[0][1].text == "[Bob] из сети A"  # форвард как есть (§12.1)
    assert a.sent == []  # обратно на A не уходит


async def test_too_long_rejected_with_notice():
    lora = FakeTransport("n1", LORA_CAPS)  # max_text_bytes=150
    m1 = FakeTransport("tg", MSG_CAPS)
    room = RoomRoute(members=(LoraMember("n1", "emergency"), MessengerMember("tg", "-100", None)))
    bridge, nodes, notices = await _build([room], {"n1": lora}, {"tg": m1})

    src = _msg("tg", "-100", "x" * 500)
    await bridge.admit(src)

    assert m1.statuses[-1][1] == DeliveryStatus.REJECTED
    assert notices  # уведомление о дропе ушло
    # в очередь ничего не попало
    await nodes["n1"].queue.close_input()
    await bridge.build_worker(nodes["n1"]).run()
    assert lora.sent == []


async def test_rate_limit_rejected():
    lora = FakeTransport("n1", LORA_CAPS)
    m1 = FakeTransport("tg", MSG_CAPS)
    room = RoomRoute(members=(LoraMember("n1", "emergency"), MessengerMember("tg", "-100", None)))
    # ёмкость 1, бёрст 1 → второе сообщение отвергается
    bridge, nodes, notices = await _build(
        [room], {"n1": lora}, {"tg": m1}, capacity=1, rate=RateSpec(1, 60, burst=1)
    )

    await bridge.admit(_msg("tg", "-100", "first", mid="a"))
    await bridge.admit(_msg("tg", "-100", "second", mid="b"))

    reject = [s for s in m1.statuses if s[1] == DeliveryStatus.REJECTED]
    assert reject
