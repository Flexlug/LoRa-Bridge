"""Graceful shutdown: очистка транспортов при отмене (LoRa-Bridge-d11).

При SIGINT/SIGTERM композиционный корень отменяет scope; `Bridge.run` должен
довести `transport.stop()` до конца, несмотря на активную отмену — иначе журнал
и соединения остаются недозакрытыми. Здесь проверяем инвариант ядра «stop() под
отменой всё равно отрабатывает»; перехват самого сигнала покрыт smoke-прогоном
живого бинаря.
"""

import anyio
import pytest

from lora_bridge.core.bridge import Bridge, MessengerBinding, NodeRuntime
from lora_bridge.core.dedup import TtlDedup
from lora_bridge.core.journal import SqliteJournal
from lora_bridge.core.loopguard import LoopGuard
from lora_bridge.core.notifier import DropNotifier
from lora_bridge.core.queue import CommitQueue
from lora_bridge.core.routing import RoomRegistry
from lora_bridge.core.status import StatusDispatcher
from lora_bridge.domain.models import LabelFormat, RateSpec
from tests.helpers.fakes import FakeTransport, LORA_CAPS, MSG_CAPS

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class StopRecordingTransport(FakeTransport):
    """FakeTransport, у которого stop() имеет точку прерывания (await) перед записью.

    Без шилда в `Bridge.run` отмена бросит Cancelled на этом await и `stop_completed`
    останется False — это и ловит регрессионный тест.
    """

    def __init__(self, *args, fail_stop: bool = False, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.stop_completed = False
        self._fail_stop = fail_stop

    async def stop(self) -> None:
        await anyio.sleep(0.01)  # checkpoint: под отменой → Cancelled, если не shielded
        if self._fail_stop:
            raise RuntimeError("stop boom")
        self.stop_completed = True
        await super().stop()


async def _build_bridge(node_transport, messengers):
    journal = SqliteJournal(":memory:")
    await journal.start()

    async def sink(ref, text):
        pass

    nodes = {
        node_transport.id: NodeRuntime(
            transport=node_transport,
            queue=CommitQueue(16, RateSpec(100, 60), ttl_seconds=45),
            dedup=TtlDedup(300),
            loop_guard=LoopGuard(60),
            label_fmt=LabelFormat(include_type=True, max_nick_bytes=24),
            commit_timeout=5,
            notifier=DropNotifier(60, sink),
        )
    }
    bindings = {mid: MessengerBinding(transport=t, tag="TG") for mid, t in messengers.items()}
    all_t = {node_transport.id: node_transport, **messengers}
    bridge = Bridge(
        nodes=nodes,
        messengers=bindings,
        rooms=RoomRegistry([]),
        status=StatusDispatcher(all_t),
        journal=journal,
    )
    return bridge, journal


async def _run_until_cancelled(bridge, *transports):
    async with anyio.create_task_group() as tg:
        tg.start_soon(bridge.run)
        with anyio.fail_after(2):
            while not all(t.started for t in transports):
                await anyio.sleep(0.005)
        tg.cancel_scope.cancel()


async def test_stop_completes_for_all_transports_under_cancel():
    lora = StopRecordingTransport("n1", LORA_CAPS)
    tg_t = StopRecordingTransport("tg", MSG_CAPS)
    bridge, journal = await _build_bridge(lora, {"tg": tg_t})
    try:
        await _run_until_cancelled(bridge, lora, tg_t)
    finally:
        await journal.stop()

    assert lora.stop_completed, "stop() LoRa-ноды пропущен под отменой (нет shield)"
    assert tg_t.stop_completed, "stop() мессенджера пропущен под отменой (нет shield)"


async def test_failing_stop_does_not_block_other_transports():
    lora = StopRecordingTransport("n1", LORA_CAPS, fail_stop=True)  # роняет stop()
    tg_t = StopRecordingTransport("tg", MSG_CAPS)
    bridge, journal = await _build_bridge(lora, {"tg": tg_t})
    try:
        await _run_until_cancelled(bridge, lora, tg_t)
    finally:
        await journal.stop()

    # упавший stop() одного транспорта не должен мешать stop() остальных
    assert tg_t.stop_completed, "падение stop() одного транспорта сорвало остановку другого"
