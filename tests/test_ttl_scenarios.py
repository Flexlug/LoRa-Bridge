"""Интеграционные тесты TTL и retry-логики (§6, §7, B1, B2, R4, B5).

Покрываем сценарии, которые нельзя проверить юнит-тестами в изоляции:
- протухание QueueItem до отправки → TTL_EXPIRED (B1)
- busy-транспорт с ретраями → SENT / FAILED (R4)
- таймаут commit → FAILED (B2)
- dedup и loop-guard в реальном пути consume() (A1, A3)
- дебаунс уведомлений о дропах в окне (B5)
"""

from __future__ import annotations

import time

import anyio
import pytest

import lora_bridge.core.egress as egress_mod
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
    RejectReason,
)
from tests.helpers.fakes import FakeClock, FakeTransport, LORA_CAPS, MSG_CAPS

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------

_open_journals: list[SqliteJournal] = []


@pytest.fixture(autouse=True)
async def close_journals():
    yield
    for j in _open_journals:
        await j.stop()
    _open_journals.clear()


def tg_msg(text: str, mid: str = "m1") -> Message:
    return Message(
        id=mid,
        source=ChannelRef("tg", "-100"),
        sender=Identity(display_name="Alex", transport_uid="u1"),
        text=text,
    )


def lora_msg(text: str, mid: str = "l1", *, origin_tag: str | None = None) -> Message:
    return Message(
        id=mid,
        source=ChannelRef("n1", "general"),
        sender=Identity(display_name="Bob", transport_uid="b1"),
        text=text,
        origin_tag=origin_tag,
    )


async def build_bridge(
    lora: FakeTransport,
    messenger: FakeTransport,
    *,
    queue_ttl: float = 45.0,
    commit_timeout: float = 5.0,
    rate: RateSpec = RateSpec(100, 60),
    notify_window: float = 60.0,
    notify_clock=None,
) -> tuple[Bridge, NodeRuntime, list]:
    journal = SqliteJournal(":memory:")
    await journal.start()
    _open_journals.append(journal)

    notices: list[tuple[ChannelRef, str]] = []

    async def sink(ref: ChannelRef, text: str) -> None:
        notices.append((ref, text))

    node = NodeRuntime(
        transport=lora,
        queue=CommitQueue(16, rate, ttl_seconds=queue_ttl),
        dedup=TtlDedup(300),
        loop_guard=LoopGuard(60),
        label_fmt=LabelFormat(include_type=False, max_nick_bytes=24),
        commit_timeout=commit_timeout,
        notifier=DropNotifier(notify_window, sink, _clock=notify_clock or time.monotonic),
    )

    bridge = Bridge(
        nodes={"n1": node},
        messengers={"tg": MessengerBinding(transport=messenger, tag="TG")},
        rooms=RoomRegistry(
            [
                RoomRoute(
                    members=(
                        LoraMember("n1", "general"),
                        MessengerMember("tg", "-100", None),
                    )
                )
            ]
        ),
        status=StatusDispatcher({"n1": lora, "tg": messenger}),
        journal=journal,
    )
    return bridge, node, notices


# ---------------------------------------------------------------------------
# B1 — протухание QueueItem до отправки
# ---------------------------------------------------------------------------


async def test_queue_item_expires_before_transmit():
    """Сообщение, пролежавшее в очереди дольше ttl_seconds, отклоняется с TTL_EXPIRED."""
    lora = FakeTransport("n1", LORA_CAPS)
    tg = FakeTransport("tg", MSG_CAPS)
    # TTL 1 мс — гарантированно истечёт после anyio.sleep(0.02)
    bridge, node, notices = await build_bridge(lora, tg, queue_ttl=0.001)

    await bridge.admit(tg_msg("привет"))
    await anyio.sleep(0.02)

    await node.queue.close_input()
    await bridge.build_worker(node).run()

    assert lora.sent == [], "протухшее сообщение не должно доходить до LoRa"
    last_status = tg.statuses[-1]
    assert last_status[1] == DeliveryStatus.REJECTED
    assert last_status[2] == RejectReason.TTL_EXPIRED
    assert notices, "уведомление о дропе должно быть отправлено"


# ---------------------------------------------------------------------------
# R4 — busy-транспорт и retry
# ---------------------------------------------------------------------------


async def test_busy_transport_retries_then_succeeds(monkeypatch):
    """Транспорт busy первые 2 попытки, 3-я успешна → статус SENT."""
    monkeypatch.setattr(egress_mod, "BUSY_BACKOFF_S", 0.0)

    lora = FakeTransport("n1", LORA_CAPS, busy_times=2)
    tg = FakeTransport("tg", MSG_CAPS)
    bridge, node, _ = await build_bridge(lora, tg)

    await bridge.admit(tg_msg("данные"))

    await node.queue.close_input()
    await bridge.build_worker(node).run()

    assert len(lora.sent) == 1
    assert tg.statuses[-1][1] == DeliveryStatus.SENT


async def test_busy_all_retries_exhausted_marks_failed(monkeypatch):
    """Транспорт busy все BUSY_RETRIES попыток → статус FAILED."""
    monkeypatch.setattr(egress_mod, "BUSY_BACKOFF_S", 0.0)

    lora = FakeTransport("n1", LORA_CAPS, busy_times=egress_mod.BUSY_RETRIES)
    tg = FakeTransport("tg", MSG_CAPS)
    bridge, node, _ = await build_bridge(lora, tg)

    await bridge.admit(tg_msg("данные"))

    await node.queue.close_input()
    await bridge.build_worker(node).run()

    assert lora.sent == []
    assert tg.statuses[-1][1] == DeliveryStatus.FAILED


# ---------------------------------------------------------------------------
# B2 — таймаут commit
# ---------------------------------------------------------------------------


async def test_commit_timeout_marks_failed():
    """Транспорт не отвечает в commit_timeout → статус FAILED."""
    lora = FakeTransport("n1", LORA_CAPS, delay=10.0)  # зависает на 10 с
    tg = FakeTransport("tg", MSG_CAPS)
    bridge, node, _ = await build_bridge(lora, tg, commit_timeout=0.01)

    await bridge.admit(tg_msg("данные"))

    await node.queue.close_input()
    await bridge.build_worker(node).run()

    assert lora.sent == []
    assert tg.statuses[-1][1] == DeliveryStatus.FAILED


# ---------------------------------------------------------------------------
# A3 — dedup в consume()
# ---------------------------------------------------------------------------


async def test_dedup_drops_duplicate_lora_message():
    """Два одинаковых LoRa-сообщения подряд: второе silently отбрасывается dedup."""
    lora = FakeTransport("n1", LORA_CAPS)
    tg = FakeTransport("tg", MSG_CAPS)
    bridge, node, _ = await build_bridge(lora, tg)

    m = lora_msg("mesh broadcast", mid="same-id")

    async with anyio.create_task_group() as tg_scope:
        tg_scope.start_soon(bridge.consume, lora)
        await anyio.sleep(0)  # даём consume() запуститься и подписаться на hub
        await lora.inject(m)
        await lora.inject(m)  # дубль — должен быть проглочен dedup
        await anyio.sleep(0)  # даём consume() обработать оба сообщения
        tg_scope.cancel_scope.cancel()

    assert len(tg.sent) == 1, "дубль не должен зеркалироваться в мессенджер"


# ---------------------------------------------------------------------------
# A1 — loop-guard в consume()
# ---------------------------------------------------------------------------


async def test_loopguard_suppresses_own_echo():
    """Текст, который нода отправила сама, не зеркалируется обратно при приёме."""
    lora = FakeTransport("n1", LORA_CAPS)
    tg = FakeTransport("tg", MSG_CAPS)
    bridge, node, _ = await build_bridge(lora, tg)

    sent_text = "Alex: поехали"
    node.loop_guard.mark_sent(sent_text)  # имитируем, что нода только что отправила это

    async with anyio.create_task_group() as tg_scope:
        tg_scope.start_soon(bridge.consume, lora)
        await anyio.sleep(0)  # даём consume() запуститься и подписаться на hub
        await lora.inject(lora_msg(sent_text))  # то же самое вернулось назад из эфира
        await anyio.sleep(0)
        tg_scope.cancel_scope.cancel()

    assert tg.sent == [], "эхо собственной передачи не должно уходить в мессенджер"


async def test_loopguard_passes_other_messages():
    """Чужие сообщения не перехватываются loop-guard."""
    lora = FakeTransport("n1", LORA_CAPS)
    tg = FakeTransport("tg", MSG_CAPS)
    bridge, node, _ = await build_bridge(lora, tg)

    node.loop_guard.mark_sent("что-то ранее отправленное")

    async with anyio.create_task_group() as tg_scope:
        tg_scope.start_soon(bridge.consume, lora)
        await anyio.sleep(0)  # даём consume() запуститься и подписаться на hub
        await lora.inject(lora_msg("совершенно другое сообщение"))
        await anyio.sleep(0)
        tg_scope.cancel_scope.cancel()

    assert len(tg.sent) == 1


# ---------------------------------------------------------------------------
# B5 — дебаунс уведомлений о дропах
# ---------------------------------------------------------------------------


async def test_notifier_sends_first_drop_immediately():
    """Первый дроп в окне → уведомление немедленно."""
    clock = FakeClock(t=0.0)
    notices: list[tuple[ChannelRef, str]] = []

    async def sink(ref: ChannelRef, text: str) -> None:
        notices.append((ref, text))

    source = ChannelRef("tg", "-100")
    notifier = DropNotifier(window_seconds=30.0, sink=sink, _clock=clock)

    await notifier.note_reject(source, RejectReason.RATE_LIMIT)
    assert len(notices) == 1


async def test_notifier_batches_drops_within_window():
    """Последующие дропы в том же окне не шлют уведомление сразу — копятся."""
    clock = FakeClock(t=0.0)
    notices: list[tuple[ChannelRef, str]] = []

    async def sink(ref: ChannelRef, text: str) -> None:
        notices.append((ref, text))

    source = ChannelRef("tg", "-100")
    notifier = DropNotifier(window_seconds=30.0, sink=sink, _clock=clock)

    await notifier.note_reject(source, RejectReason.RATE_LIMIT)  # сразу
    await notifier.note_reject(source, RejectReason.RATE_LIMIT)  # копится
    await notifier.note_reject(source, RejectReason.RATE_LIMIT)  # копится

    assert len(notices) == 1, "только первый дроп уходит немедленно"

    # flush до истечения окна — ничего не должно вылететь
    clock.t = 20.0
    await notifier.flush_due()
    assert len(notices) == 1


async def test_notifier_flushes_batch_after_window():
    """После истечения окна flush_due() шлёт агрегированное уведомление."""
    clock = FakeClock(t=0.0)
    notices: list[tuple[ChannelRef, str]] = []

    async def sink(ref: ChannelRef, text: str) -> None:
        notices.append((ref, text))

    source = ChannelRef("tg", "-100")
    notifier = DropNotifier(window_seconds=30.0, sink=sink, _clock=clock)

    await notifier.note_reject(source, RejectReason.RATE_LIMIT)
    await notifier.note_reject(source, RejectReason.RATE_LIMIT)
    await notifier.note_reject(source, RejectReason.RATE_LIMIT)

    clock.t = 31.0
    await notifier.flush_due()

    assert len(notices) == 2
    assert "2" in notices[1][1], "в агрегированном уведомлении должен быть счётчик (2 накоплено)"


async def test_notifier_different_reasons_are_independent():
    """TOO_LONG и RATE_LIMIT — разные окна, не мешают друг другу."""
    clock = FakeClock(t=0.0)
    notices: list[tuple[ChannelRef, str]] = []

    async def sink(ref: ChannelRef, text: str) -> None:
        notices.append((ref, text))

    source = ChannelRef("tg", "-100")
    notifier = DropNotifier(window_seconds=30.0, sink=sink, _clock=clock)

    await notifier.note_reject(source, RejectReason.RATE_LIMIT)
    await notifier.note_reject(source, RejectReason.TOO_LONG)

    assert len(notices) == 2, "разные причины — независимые окна, оба уходят сразу"
