"""Сквозные интеграционные тесты: YAML-конфиг → wiring.py → Bridge → транспорт.

Это единственные тесты, которые проходят полный путь запуска:
  1. Парсинг YAML-конфига в AppConfig
  2. Сборка графа объектов через wiring.py (build_lora_nodes, build_messengers, build_rooms)
  3. Прохождение сообщения от источника до получателя на fake-транспортах

Реальные транспорты (MeshCoreTransport, TelegramTransport) подменяются через monkeypatch
непосредственно в пространстве имён wiring.py — остальная логика сборки (NodeRuntime,
CommitQueue, LoopGuard, ...) работает без изменений.
"""

from __future__ import annotations

import yaml
import pytest
import anyio

import lora_bridge.wiring as wiring_mod
from lora_bridge.config.schema import AppConfig
from lora_bridge.core.bridge import Bridge
from lora_bridge.core.journal import SqliteJournal
from lora_bridge.core.notifier import DropNotifier
from lora_bridge.core.status import StatusDispatcher
from lora_bridge.domain.models import ChannelRef, DeliveryStatus, Identity, Message, RejectReason
from lora_bridge.wiring import build_lora_nodes, build_messengers, build_rooms

from tests.helpers.fakes import FakeTransport, LORA_CAPS, MSG_CAPS

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# Инфраструктура
# ---------------------------------------------------------------------------

_CFG_ONE_ROOM = """
lora:
  - id: mc-1
    type: meshcore
    connection:
      type: tcp
      host: "127.0.0.1"
      port: 5000
    endpoints:
      general:
        type: public
        channel_name: "General"
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
messengers:
  - id: tg
    kind: telegram
    token: "123:ABC"
rooms:
  - lora:
      node: mc-1
      endpoint: general
    subscribers:
      - transport: tg
        chat: "-100"
"""

_CFG_TIGHT_RATE = """
lora:
  - id: mc-1
    type: meshcore
    connection:
      type: tcp
      host: "127.0.0.1"
      port: 5000
    endpoints:
      general:
        type: public
        channel_name: "General"
    policies:
      egress_rate:
        msgs_per_window: 1
        window_seconds: 60
messengers:
  - id: tg
    kind: telegram
    token: "123:ABC"
rooms:
  - lora:
      node: mc-1
      endpoint: general
    subscribers:
      - transport: tg
        chat: "-100"
"""

_CFG_SHORT_TTL = """
lora:
  - id: mc-1
    type: meshcore
    connection:
      type: tcp
      host: "127.0.0.1"
      port: 5000
    endpoints:
      general:
        type: public
        channel_name: "General"
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
      queue_ttl_seconds: 0.001
messengers:
  - id: tg
    kind: telegram
    token: "123:ABC"
rooms:
  - lora:
      node: mc-1
      endpoint: general
    subscribers:
      - transport: tg
        chat: "-100"
"""

_CFG_COMMIT_TIMEOUT = """
lora:
  - id: mc-1
    type: meshcore
    connection:
      type: tcp
      host: "127.0.0.1"
      port: 5000
    endpoints:
      general:
        type: public
        channel_name: "General"
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
      commit_timeout_seconds: 0.01
messengers:
  - id: tg
    kind: telegram
    token: "123:ABC"
rooms:
  - lora:
      node: mc-1
      endpoint: general
    subscribers:
      - transport: tg
        chat: "-100"
"""

_CFG_TWO_TG_SUBS = """
lora:
  - id: mc-1
    type: meshcore
    connection:
      type: tcp
      host: "127.0.0.1"
      port: 5000
    endpoints:
      general:
        type: public
        channel_name: "General"
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
messengers:
  - id: tg
    kind: telegram
    token: "123:ABC"
rooms:
  - lora:
      node: mc-1
      endpoint: general
    subscribers:
      - transport: tg
        chat: "-100"
      - transport: tg
        chat: "-200"
"""

_CFG_LABEL_NEVER = """
lora:
  - id: mc-1
    type: meshcore
    connection:
      type: tcp
      host: "127.0.0.1"
      port: 5000
    endpoints:
      general:
        type: public
        channel_name: "General"
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
      label:
        include_type: never
messengers:
  - id: tg
    kind: telegram
    token: "123:ABC"
rooms:
  - lora:
      node: mc-1
      endpoint: general
    subscribers:
      - transport: tg
        chat: "-100"
      - transport: tg
        chat: "-200"
"""

_CFG_LORA_TO_LORA = """
lora:
  - id: mc-1
    type: meshcore
    connection:
      type: tcp
      host: "127.0.0.1"
      port: 5000
    endpoints:
      ch:
        type: public
        channel_name: "General"
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
  - id: mc-2
    type: meshcore
    connection:
      type: tcp
      host: "127.0.0.1"
      port: 5001
    endpoints:
      relay:
        type: public
        channel_name: "General"
    policies:
      egress_rate:
        msgs_per_window: 6
        window_seconds: 60
messengers: []
rooms:
  - lora:
      node: mc-1
      endpoint: ch
    subscribers:
      - lora:
          node: mc-2
          endpoint: relay
"""


@pytest.fixture
def wire_fakes(monkeypatch):
    """Подменяет MeshCoreTransport и TelegramTransport в wiring.py на FakeTransport.

    Возвращает два словаря (lora_fakes, tg_fakes), ключи — transport id.
    """
    lora_fakes: dict[str, FakeTransport] = {}
    tg_fakes: dict[str, FakeTransport] = {}

    def make_lora(node):
        t = FakeTransport(node.id, LORA_CAPS)
        lora_fakes[node.id] = t
        return t

    def make_tg(transport_id, tag, cfg):
        t = FakeTransport(transport_id, MSG_CAPS)
        tg_fakes[transport_id] = t
        return t

    monkeypatch.setattr(wiring_mod, "MeshCoreTransport", make_lora)
    monkeypatch.setattr(wiring_mod, "TelegramTransport", make_tg)
    return lora_fakes, tg_fakes


async def assemble(yaml_text: str) -> tuple[Bridge, dict, SqliteJournal]:
    """Парсинг YAML → wiring → Bridge. Возвращает bridge, runtimes ноды, journal."""
    cfg = AppConfig.model_validate(yaml.safe_load(yaml_text))
    lora = build_lora_nodes(cfg)
    messengers = build_messengers(cfg)

    journal = SqliteJournal(":memory:")
    await journal.start()

    status = StatusDispatcher({**lora.transports, **messengers.transports})

    async def _sink(ref, text):
        pass

    bridge = Bridge(
        nodes=lora.runtimes,
        messengers=messengers.transports,
        tags=messengers.tags,
        rooms=build_rooms(cfg),
        status=status,
        notifier=DropNotifier(60, _sink),
        journal=journal,
    )
    return bridge, lora.runtimes, journal


# ---------------------------------------------------------------------------
# Сборка
# ---------------------------------------------------------------------------


async def test_wiring_builds_node_runtime(wire_fakes):
    """wiring.py строит NodeRuntime с очередью и dedup вокруг fake-транспорта."""
    lora_fakes, _ = wire_fakes
    _, runtimes, journal = await assemble(_CFG_ONE_ROOM)
    await journal.stop()

    assert "mc-1" in runtimes
    runtime = runtimes["mc-1"]
    assert runtime.transport is lora_fakes["mc-1"]
    assert runtime.queue is not None
    assert runtime.dedup is not None


# ---------------------------------------------------------------------------
# Telegram → LoRa
# ---------------------------------------------------------------------------


async def test_tg_to_lora_full_path(wire_fakes):
    """Сообщение из Telegram проходит: admit → очередь → worker → LoRa-транспорт."""
    lora_fakes, _ = wire_fakes
    bridge, runtimes, journal = await assemble(_CFG_ONE_ROOM)

    msg = Message(
        id="m1",
        source=ChannelRef("tg", "-100"),
        sender=Identity(display_name="Alex", transport_uid="u1"),
        text="привет",
    )
    await bridge.admit(msg)

    node = runtimes["mc-1"]
    await node.queue.close_input()
    await bridge.build_worker(node).run()
    await journal.stop()

    assert len(lora_fakes["mc-1"].sent) == 1
    _, sent_msg = lora_fakes["mc-1"].sent[0]
    assert "привет" in sent_msg.text


async def test_tg_to_lora_status_sent(wire_fakes):
    """После успешной отправки в LoRa статус источника становится SENT."""
    _, tg_fakes = wire_fakes
    bridge, runtimes, journal = await assemble(_CFG_ONE_ROOM)

    await bridge.admit(
        Message(
            id="m1",
            source=ChannelRef("tg", "-100"),
            sender=Identity(display_name="Alex", transport_uid="u1"),
            text="test",
        )
    )

    node = runtimes["mc-1"]
    await node.queue.close_input()
    await bridge.build_worker(node).run()
    await journal.stop()

    terminal = [s for s in tg_fakes["tg"].statuses if s[1] == DeliveryStatus.SENT]
    assert terminal, "должен быть терминальный статус SENT"


# ---------------------------------------------------------------------------
# LoRa → Telegram
# ---------------------------------------------------------------------------


async def test_lora_to_tg_full_path(wire_fakes):
    """Сообщение из LoRa проходит: inject → consume → Telegram-транспорт."""
    lora_fakes, tg_fakes = wire_fakes
    bridge, _, journal = await assemble(_CFG_ONE_ROOM)

    lora_transport = lora_fakes["mc-1"]
    incoming = Message(
        id="l1",
        source=ChannelRef("mc-1", "general"),
        sender=Identity(display_name="Bob", transport_uid="b1"),
        text="из эфира",
    )

    async with anyio.create_task_group() as tg:
        tg.start_soon(bridge.consume, lora_transport)
        await anyio.sleep(0)  # даём consume() подписаться на hub
        await lora_transport.inject(incoming)
        await anyio.sleep(0)  # даём consume() обработать
        tg.cancel_scope.cancel()

    await journal.stop()

    assert len(tg_fakes["tg"].sent) == 1
    _, sent = tg_fakes["tg"].sent[0]
    assert "из эфира" in sent.text


# ---------------------------------------------------------------------------
# LoRa ↔ LoRa
# ---------------------------------------------------------------------------


async def test_lora_to_lora_full_path(wire_fakes):
    """Сообщение с mc-1 проходит через relay: inject → consume → worker mc-2 → отправлено."""
    lora_fakes, _ = wire_fakes
    bridge, runtimes, journal = await assemble(_CFG_LORA_TO_LORA)

    incoming = Message(
        id="l1",
        source=ChannelRef("mc-1", "ch"),
        sender=Identity(display_name="Bob", transport_uid="b1"),
        text="relay test",
    )

    async with anyio.create_task_group() as tg:
        tg.start_soon(bridge.consume, lora_fakes["mc-1"])
        await anyio.sleep(0)
        await lora_fakes["mc-1"].inject(incoming)
        await anyio.sleep(0)
        tg.cancel_scope.cancel()

    node2 = runtimes["mc-2"]
    await node2.queue.close_input()
    await bridge.build_worker(node2).run()
    await journal.stop()

    assert len(lora_fakes["mc-2"].sent) == 1
    assert lora_fakes["mc-1"].sent == []  # обратно на mc-1 не уходит


# ---------------------------------------------------------------------------
# Sad path: конфиг валиден схемой, но поведение — отказ / потеря
# ---------------------------------------------------------------------------


async def test_rate_limit_from_config(wire_fakes):
    """egress_rate: 1 msg/60s в конфиге → второе сообщение отклоняется с RATE_LIMIT."""
    _, tg_fakes = wire_fakes
    bridge, runtimes, journal = await assemble(_CFG_TIGHT_RATE)

    def _msg(mid):
        return Message(
            id=mid,
            source=ChannelRef("tg", "-100"),
            sender=Identity(display_name="Alex", transport_uid="u1"),
            text="x",
        )

    await bridge.admit(_msg("m1"))  # проходит
    await bridge.admit(_msg("m2"))  # rate limit
    await journal.stop()

    rejected = [s for s in tg_fakes["tg"].statuses if s[1] == DeliveryStatus.REJECTED]
    assert len(rejected) == 1
    assert rejected[0][2] == RejectReason.RATE_LIMIT


async def test_too_long_message_rejected(wire_fakes):
    """Сообщение длиннее 150 байт отклоняется с TOO_LONG до попадания в очередь."""
    lora_fakes, tg_fakes = wire_fakes
    bridge, runtimes, journal = await assemble(_CFG_ONE_ROOM)

    await bridge.admit(
        Message(
            id="m1",
            source=ChannelRef("tg", "-100"),
            sender=Identity(display_name="X", transport_uid="u1"),
            text="я" * 100,  # 200 байт в UTF-8 (кириллица 2 байта) > 150-байтового лимита LoRa
        )
    )
    await journal.stop()

    assert lora_fakes["mc-1"].sent == []
    rejected = [s for s in tg_fakes["tg"].statuses if s[1] == DeliveryStatus.REJECTED]
    assert rejected and rejected[0][2] == RejectReason.TOO_LONG


async def test_short_ttl_from_config_expires_message(wire_fakes):
    """queue_ttl_seconds: 0.001 в конфиге → сообщение протухает до отправки."""
    lora_fakes, tg_fakes = wire_fakes
    bridge, runtimes, journal = await assemble(_CFG_SHORT_TTL)

    await bridge.admit(
        Message(
            id="m1",
            source=ChannelRef("tg", "-100"),
            sender=Identity(display_name="Alex", transport_uid="u1"),
            text="привет",
        )
    )
    await anyio.sleep(0.02)  # TTL 1 мс истёк

    node = runtimes["mc-1"]
    await node.queue.close_input()
    await bridge.build_worker(node).run()
    await journal.stop()

    assert lora_fakes["mc-1"].sent == []
    expired = [s for s in tg_fakes["tg"].statuses if s[1] == DeliveryStatus.REJECTED]
    assert expired and expired[0][2] == RejectReason.TTL_EXPIRED


async def test_message_from_unconfigured_chat_is_dropped(wire_fakes):
    """Сообщение из чата вне rooms тихо игнорируется — без статуса, без очереди."""
    lora_fakes, tg_fakes = wire_fakes
    bridge, runtimes, journal = await assemble(_CFG_ONE_ROOM)

    await bridge.admit(
        Message(
            id="m1",
            source=ChannelRef("tg", "-999"),  # чат не прописан в rooms
            sender=Identity(display_name="Ghost", transport_uid="g1"),
            text="привет",
        )
    )
    await journal.stop()

    assert lora_fakes["mc-1"].sent == []
    assert tg_fakes["tg"].statuses == []  # никакого статуса — сообщение проглочено


async def test_lora_message_to_unconfigured_endpoint_is_dropped(wire_fakes):
    """Сообщение с LoRa-эндпоинта не из rooms тихо игнорируется."""
    lora_fakes, tg_fakes = wire_fakes
    bridge, _, journal = await assemble(_CFG_ONE_ROOM)

    ghost_msg = Message(
        id="l1",
        source=ChannelRef("mc-1", "unknown-endpoint"),  # эндпоинт не в rooms
        sender=Identity(display_name="Bob", transport_uid="b1"),
        text="никто не услышит",
    )

    async with anyio.create_task_group() as tg:
        tg.start_soon(bridge.consume, lora_fakes["mc-1"])
        await anyio.sleep(0)
        await lora_fakes["mc-1"].inject(ghost_msg)
        await anyio.sleep(0)
        tg.cancel_scope.cancel()

    await journal.stop()

    assert tg_fakes["tg"].sent == []


async def test_commit_timeout_from_config(monkeypatch):
    """commit_timeout_seconds: 0.01 + медленный транспорт → воркер таймаутится → FAILED."""
    slow_lora = FakeTransport("mc-1", LORA_CAPS, delay=10.0)
    tg = FakeTransport("tg", MSG_CAPS)
    monkeypatch.setattr(wiring_mod, "MeshCoreTransport", lambda node: slow_lora)
    monkeypatch.setattr(wiring_mod, "TelegramTransport", lambda tid, tag, cfg: tg)

    bridge, runtimes, journal = await assemble(_CFG_COMMIT_TIMEOUT)

    await bridge.admit(
        Message(
            id="m1",
            source=ChannelRef("tg", "-100"),
            sender=Identity(display_name="Alex", transport_uid="u1"),
            text="test",
        )
    )

    node = runtimes["mc-1"]
    await node.queue.close_input()
    await bridge.build_worker(node).run()
    await journal.stop()

    assert slow_lora.sent == []
    assert any(s[1] == DeliveryStatus.FAILED for s in tg.statuses)


async def test_lora_transport_failure_marks_failed(monkeypatch):
    """LoRa-транспорт явно возвращает failure → статус FAILED, не исключение."""
    failing_lora = FakeTransport("mc-1", LORA_CAPS, fail=True)
    tg = FakeTransport("tg", MSG_CAPS)
    monkeypatch.setattr(wiring_mod, "MeshCoreTransport", lambda node: failing_lora)
    monkeypatch.setattr(wiring_mod, "TelegramTransport", lambda tid, tag, cfg: tg)

    bridge, runtimes, journal = await assemble(_CFG_ONE_ROOM)

    await bridge.admit(
        Message(
            id="m1",
            source=ChannelRef("tg", "-100"),
            sender=Identity(display_name="Alex", transport_uid="u1"),
            text="test",
        )
    )

    node = runtimes["mc-1"]
    await node.queue.close_input()
    await bridge.build_worker(node).run()
    await journal.stop()

    assert failing_lora.sent == []
    assert any(s[1] == DeliveryStatus.FAILED for s in tg.statuses)


async def test_queue_overflow_rejected(wire_fakes, monkeypatch):
    """QUEUE_CAPACITY=2 → третье сообщение не помещается в буфер → RATE_LIMIT."""
    monkeypatch.setattr(wiring_mod, "QUEUE_CAPACITY", 2)
    _, tg_fakes = wire_fakes
    bridge, _, journal = await assemble(_CFG_ONE_ROOM)

    def _msg(mid):
        return Message(
            id=mid,
            source=ChannelRef("tg", "-100"),
            sender=Identity(display_name="Alex", transport_uid="u1"),
            text="x",
        )

    await bridge.admit(_msg("m1"))
    await bridge.admit(_msg("m2"))
    await bridge.admit(_msg("m3"))  # WouldBlock → RATE_LIMIT
    await journal.stop()

    rejected = [s for s in tg_fakes["tg"].statuses if s[1] == DeliveryStatus.REJECTED]
    assert len(rejected) == 1
    assert rejected[0][2] == RejectReason.RATE_LIMIT


# ---------------------------------------------------------------------------
# Happy path через wiring: поведение которое не тестируется в test_pipeline.py
# ---------------------------------------------------------------------------


async def test_mirror_to_second_subscriber_after_commit(wire_fakes):
    """После commit в LoRa оригинал зеркалируется во второй чат, но не обратно в источник."""
    lora_fakes, tg_fakes = wire_fakes
    bridge, runtimes, journal = await assemble(_CFG_TWO_TG_SUBS)

    await bridge.admit(
        Message(
            id="m1",
            source=ChannelRef("tg", "-100"),
            sender=Identity(display_name="Alex", transport_uid="u1"),
            text="привет",
        )
    )

    node = runtimes["mc-1"]
    await node.queue.close_input()
    await bridge.build_worker(node).run()
    await journal.stop()

    assert len(lora_fakes["mc-1"].sent) == 1
    sent_chats = [ref.channel for ref, _ in tg_fakes["tg"].sent]
    assert "-200" in sent_chats  # зеркало ушло во второй чат
    assert "-100" not in sent_chats  # в источник не возвращается


async def test_label_never_omits_type_prefix(wire_fakes):
    """include_type: never → [Alex] вместо [TG:Alex] даже при двух мессенджерах в комнате."""
    lora_fakes, _ = wire_fakes
    bridge, runtimes, journal = await assemble(_CFG_LABEL_NEVER)

    await bridge.admit(
        Message(
            id="m1",
            source=ChannelRef("tg", "-100"),
            sender=Identity(display_name="Alex", transport_uid="u1"),
            text="привет",
        )
    )

    node = runtimes["mc-1"]
    await node.queue.close_input()
    await bridge.build_worker(node).run()
    await journal.stop()

    _, sent_msg = lora_fakes["mc-1"].sent[0]
    assert "TG" not in sent_msg.text
    assert "Alex" in sent_msg.text
    assert "привет" in sent_msg.text


async def test_dedup_same_lora_message_twice(wire_fakes):
    """Одно и то же сообщение из LoRa дважды → dedup пропускает только первое."""
    lora_fakes, tg_fakes = wire_fakes
    bridge, _, journal = await assemble(_CFG_ONE_ROOM)

    msg = Message(
        id="l1",
        source=ChannelRef("mc-1", "general"),
        sender=Identity(display_name="Bob", transport_uid="b1"),
        text="дубль",
    )

    async with anyio.create_task_group() as tg:
        tg.start_soon(bridge.consume, lora_fakes["mc-1"])
        await anyio.sleep(0)
        await lora_fakes["mc-1"].inject(msg)
        await lora_fakes["mc-1"].inject(msg)  # тот же текст → dedup
        await anyio.sleep(0)
        tg.cancel_scope.cancel()

    await journal.stop()

    assert len(tg_fakes["tg"].sent) == 1


async def test_loopguard_suppresses_echo_through_wiring(wire_fakes):
    """После TX в LoRa эхо того же текста из эфира подавляется loop-guard."""
    lora_fakes, tg_fakes = wire_fakes
    bridge, runtimes, journal = await assemble(_CFG_ONE_ROOM)

    # Шаг 1: сообщение из TG уходит в LoRa, loop_guard запоминает отправленный текст
    await bridge.admit(
        Message(
            id="m1",
            source=ChannelRef("tg", "-100"),
            sender=Identity(display_name="Alex", transport_uid="u1"),
            text="привет",
        )
    )
    node = runtimes["mc-1"]
    await node.queue.close_input()
    await bridge.build_worker(node).run()

    assert len(lora_fakes["mc-1"].sent) == 1
    sent_text = lora_fakes["mc-1"].sent[0][1].text  # "[Alex] привет"

    # Шаг 2: то же самое возвращается из эфира — должно быть подавлено
    echo = Message(
        id="echo-1",
        source=ChannelRef("mc-1", "general"),
        sender=Identity(display_name="anon", transport_uid="anon"),
        text=sent_text,
    )
    async with anyio.create_task_group() as tg:
        tg.start_soon(bridge.consume, lora_fakes["mc-1"])
        await anyio.sleep(0)
        await lora_fakes["mc-1"].inject(echo)
        await anyio.sleep(0)
        tg.cancel_scope.cancel()

    await journal.stop()

    assert tg_fakes["tg"].sent == []
